"""The tailoring agent: read a job's requirements, search the user's evidence, then propose a
grounded rewrite or flag a gap.

The model proposes and the backend authorizes. A proposed edit is rejected unless every cited
bullet belongs to this user and was returned by a search in this same run, so the model can't
cite what it never found. Rejections go back to it as tool results, to search again.
"""

import json
import logging
import time

from openai import OpenAI
from services.claim_check import unsupported_claims
from services.match import normalize_skill
from services.openai_services import usd
from services.resume_evidence import evidence_is_stale
from services.resume_search import search_resume_bullets
from services.skill_evidence import match_for_job
from services.usage import QuotaExceeded, check_quota, record

logger = logging.getLogger(__name__)
client = OpenAI()

MODEL = "gpt-4o-mini"
DEFAULT_MAX_STEPS = 12
MAX_PROPOSED_TEXT_CHARS = 500
MAX_EVIDENCE_PER_EDIT = 5


SYSTEM_PROMPT = """You tailor a candidate's resume bullets to one job posting. You work only from evidence you find.

You are given a requirement-by-requirement assessment computed before you started. Use it to spend your
budget where it pays:

- INFERRED or PARTIAL — the highest-value cases. The candidate can evidently do this, but the resume does
  not say so plainly. Search for the evidence, then propose an edit that makes it explicit.
- EXPLICIT — already stated. Only revisit one if the wording badly undersells it.
- NONE — nothing in the resume supports it. Call flag_gap directly; do not spend searches confirming an
  absence that has already been established.

Work ONE requirement at a time and finish it before starting the next: search, then immediately either
propose_edit or flag_gap. Do not run many searches back to back — you have a limited number of steps, and a
run that only searches produces nothing for the user.

Per requirement:
1. Call search_resume for it.
2. If the results are thin or only partly relevant, search ONCE more with related wording, adjacent tools,
   or the underlying activity.
3. Then either:
   - propose_edit — rewrite ONE existing bullet so it speaks to the requirement, citing the bullet ids you actually found; or
   - flag_gap — say the candidate does not have this experience.

Hard rules:
- Never state an accomplishment, technology, metric, or responsibility that is not in the evidence you retrieved. You may strengthen the wording; you may not strengthen the facts. Numbers especially: never introduce a percentage, count, or multiple that the evidence does not already contain.
- You may only cite bullet ids returned to you by search_resume in this session.
- Prefer flag_gap over a stretched claim. Flagging an honest gap is a correct outcome, not a failure.
- Keep a proposed bullet to one sentence or two, in the candidate's own register.
When you have covered the important requirements, reply with a short plain-text summary and no tool call."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_resume",
            "description": "Search the candidate's resume evidence for a requirement, skill, or activity. Returns matching bullets with their ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A requirement, skill, or activity to look for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_edit",
            "description": "Propose a rewrite of one existing bullet, grounded in evidence you retrieved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string", "description": "The job requirement this addresses."},
                    "bullet_id": {"type": "string", "description": "The bullet being rewritten."},
                    "proposed_text": {"type": "string", "description": "The rewritten bullet."},
                    "evidence_bullet_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Bullet ids from search_resume that support this text.",
                    },
                },
                "required": ["requirement", "bullet_id", "proposed_text", "evidence_bullet_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_gap",
            "description": "Record that the candidate has no evidence for a requirement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "note": {"type": "string", "description": "What was searched and what was missing."},
                },
                "required": ["requirement"],
            },
        },
    },
]


class GroundingError(Exception):
    """A check the model failed. Goes back to it as a tool result, never to the user."""


def job_brief(job, assessment=None):
    """The opening message: the posting, plus the fit we already computed.

    Handing over the assessment saves the agent from rediscovering it by search. No bullet ids
    though — citations still have to come from a real search, or the prompt itself would
    satisfy the grounding check.
    """
    title, company, summary, skills = job
    lines = [f"Job title: {title or 'unknown'}", f"Company: {company or 'unknown'}"]
    if summary:
        lines.append(f"Summary: {summary}")
    if skills:
        lines.append("Required skills: " + ", ".join(skills))

    if assessment and assessment.get("requirements"):
        lines.append("")
        lines.append("Assessment of this candidate against the posting:")
        for item in assessment["requirements"]:
            note = f"- {item['requirement']} [{item['state']}"
            if item.get("importance") and item["importance"] != "required":
                note += f", {item['importance'].replace('_', ' ')}"
            note += "]"
            if item.get("inferred_from"):
                note += f" — implied by {', '.join(item['inferred_from'])}"
            lines.append(note)
        lines.append("")
        lines.append(
            f"Capability {assessment['capability_score']}% · literal keyword coverage "
            f"{assessment['keyword_score']}%. The difference is what you are here to close."
        )

    return "\n".join(lines)


# the three tools. user_id comes from the server; the model never names a user.

def tool_search_resume(cur, user_id, run_id, arguments):
    query = arguments.get("query")
    results = search_resume_bullets(cur, user_id, query or "", limit=5)
    return {"query": query, "results": results, "count": len(results)}


def verify_citation(cur, user_id, run_id, bullet_id):
    """The tool call that surfaced this bullet in this run, or an error. Two conditions,
    both checked in SQL: the bullet is this user's, and a search here actually returned it."""
    cur.execute(
        "SELECT 1 FROM resume_bullets WHERE id = %s AND user_id = %s",
        (bullet_id, user_id),
    )
    if cur.fetchone() is None:
        raise GroundingError(f"bullet {bullet_id} is not part of your resume")

    cur.execute(
        """
        SELECT id
        FROM tool_calls
        WHERE run_id = %s
          AND tool_name = 'search_resume'
          AND status = 'completed'
          AND result -> 'results' @> jsonb_build_array(jsonb_build_object('bullet_id', %s::text))
        ORDER BY step_number
        LIMIT 1
        """,
        (run_id, str(bullet_id)),
    )
    row = cur.fetchone()
    if row is None:
        raise GroundingError(f"bullet {bullet_id} was not returned by a search in this run — search for it first")
    return row[0]


def tool_propose_edit(cur, user_id, run_id, arguments):
    requirement = (arguments.get("requirement") or "").strip()
    bullet_id = (arguments.get("bullet_id") or "").strip()
    proposed_text = (arguments.get("proposed_text") or "").strip()
    evidence_ids = arguments.get("evidence_bullet_ids") or []

    if not requirement or not proposed_text:
        raise GroundingError("requirement and proposed_text are both required")
    if len(proposed_text) > MAX_PROPOSED_TEXT_CHARS:
        raise GroundingError(f"proposed_text must be {MAX_PROPOSED_TEXT_CHARS} characters or fewer")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise GroundingError("evidence_bullet_ids is required — an edit with no evidence is not allowed")

    # the rewritten bullet is a claim too, so it gets verified the same way
    cited = list(dict.fromkeys([bullet_id] + [str(i) for i in evidence_ids]))[:MAX_EVIDENCE_PER_EDIT + 1]
    links = {}
    for candidate in cited:
        links[candidate] = verify_citation(cur, user_id, run_id, candidate)

    # citing real bullets isn't enough: the new sentence has to stay inside them
    cur.execute(
        "SELECT text FROM resume_bullets WHERE user_id = %s AND id = ANY(%s::uuid[])",
        (user_id, list(links)),
    )
    invented = unsupported_claims(proposed_text, [row[0] for row in cur.fetchall()])
    if invented:
        raise GroundingError(
            f"{', '.join(invented)} does not appear in the evidence you cited — "
            "rewrite using only what those bullets say, or cite a bullet that supports it"
        )

    cur.execute(
        """
        INSERT INTO proposed_edits (run_id, user_id, bullet_id, requirement, proposed_text)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (run_id, user_id, bullet_id, requirement, proposed_text),
    )
    edit_id = cur.fetchone()[0]

    for cited_bullet, tool_call_id in links.items():
        cur.execute(
            """
            INSERT INTO evidence_links (edit_id, bullet_id, tool_call_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (edit_id, bullet_id) DO NOTHING
            """,
            (edit_id, cited_bullet, tool_call_id),
        )

    return {"edit_id": str(edit_id), "cited_bullets": len(links), "status": "recorded"}


def tool_flag_gap(cur, user_id, run_id, arguments):
    requirement = (arguments.get("requirement") or "").strip()
    if not requirement:
        raise GroundingError("requirement is required")

    cur.execute(
        """
        SELECT arguments ->> 'query', COALESCE((result ->> 'count')::int, 0)
        FROM tool_calls
        WHERE run_id = %s AND tool_name = 'search_resume' AND status = 'completed'
        """,
        (run_id,),
    )
    searches = [(row[0], row[1]) for row in cur.fetchall() if row[0]]
    searched = list(dict.fromkeys(query for query, _ in searches))

    # a gap claims the evidence isn't there; if this run's own search found some, it isn't true
    target = normalize_skill(requirement)
    found = sum(count for query, count in searches if normalize_skill(query) == target)
    if found:
        raise GroundingError(
            f"a search for {requirement} in this run returned {found} bullet(s) — "
            "propose an edit citing them, or search again with different wording"
        )

    cur.execute(
        """
        INSERT INTO gaps (run_id, user_id, requirement, note, searched)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (run_id, user_id, requirement, (arguments.get("note") or "").strip() or None, json.dumps(searched)),
    )
    return {"gap_id": str(cur.fetchone()[0]), "status": "recorded"}


TOOL_IMPLEMENTATIONS = {
    "search_resume": tool_search_resume,
    "propose_edit": tool_propose_edit,
    "flag_gap": tool_flag_gap,
}


def complete(messages):
    """One model call. Split out so tests can script the loop without an API key."""
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOLS,
        temperature=0,
        timeout=60,
    )


def execute_tool(cur, user_id, run_id, step, call):
    """Run one tool call and record it. A rejection is a failed call handed back to the
    model, not an error the user sees."""
    name = call.function.name
    try:
        arguments = json.loads(call.function.arguments or "{}")
        if not isinstance(arguments, dict):
            raise GroundingError("tool arguments must be an object")
    except json.JSONDecodeError:
        arguments = {}
        result, error = None, "arguments were not valid JSON"
    else:
        implementation = TOOL_IMPLEMENTATIONS.get(name)
        if implementation is None:
            result, error = None, f"unknown tool {name}"
        else:
            try:
                result, error = implementation(cur, user_id, run_id, arguments), None
            except GroundingError as exc:
                result, error = None, str(exc)

    cur.execute(
        """
        INSERT INTO tool_calls (run_id, step_number, call_id, tool_name, arguments, result, status, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, call_id) DO NOTHING
        """,
        (
            run_id, step, call.id, name, json.dumps(arguments),
            json.dumps(result) if result is not None else None,
            "completed" if error is None else "failed",
            error,
        ),
    )

    return result if error is None else {"error": error}


def start_run(get_cursor, user_id, job_id, max_steps=DEFAULT_MAX_STEPS):
    """Create the run row, or refuse cheaply. Returns a run id, None if the job isn't this
    user's, or {"error": "no_evidence"}. Split from execution so the caller can hand the id
    back and let the UI watch the run happen."""
    max_steps = max(1, min(int(max_steps), 20))

    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT title, company_name, summary, skills FROM jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        job = cur.fetchone()
        if job is None:
            return None                     # not this user's job — the route turns this into a 404

        cur.execute("SELECT count(*) FROM resume_bullets WHERE user_id = %s", (user_id,))
        if cur.fetchone()[0] == 0:
            return {"error": "no_evidence"}  # nothing to cite, so nothing worth proposing
        if evidence_is_stale(cur, user_id):
            return {"error": "stale_evidence"}   # it would cite a resume they replaced

    try:
        check_quota(user_id)     # refuse before creating a run row at all
    except QuotaExceeded as exc:
        return {"error": "quota_exceeded", "detail": str(exc)}

    with get_cursor(commit=True) as cur:

        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, job_id, MODEL, max_steps),
        )
        return cur.fetchone()[0]


def execute_run(get_cursor, user_id, job_id, run_id, max_steps=DEFAULT_MAX_STEPS):
    """Drive an already-created run to completion."""
    max_steps = max(1, min(int(max_steps), 20))

    with get_cursor() as cur:
        cur.execute(
            "SELECT title, company_name, summary, skills, requirements FROM jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        row = cur.fetchone()
        job = row[:4]
        assessment = match_for_job(cur, user_id, row[4], row[3])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": job_brief(job, assessment)},
    ]
    status, error_code, summary = "limit_reached", None, None
    input_tokens = output_tokens = steps_used = 0
    t0 = time.perf_counter()

    for step in range(1, max_steps + 1):
        try:
            check_quota(user_id)
        except QuotaExceeded:
            status, error_code = "limit_reached", "quota_exceeded"
            break

        # beat before the call, not only after it: a 60s model call would otherwise eat
        # most of the timeout budget and a retried one could look abandoned
        with get_cursor(commit=True) as cur:
            cur.execute("UPDATE tailoring_runs SET heartbeat_at = now() WHERE id = %s", (run_id,))

        try:
            response = complete(messages)          # no DB connection held here
        except Exception:
            logger.exception("tailoring run failed run_id=%s step=%d", run_id, step)
            status, error_code = "failed", "model_call_failed"
            break

        steps_used = step
        if response.usage:
            input_tokens += response.usage.prompt_tokens
            output_tokens += response.usage.completion_tokens
            record(user_id, "tailoring_step", MODEL, response.usage.prompt_tokens,
                   response.usage.completion_tokens, 0, run_id=run_id)

        message = response.choices[0].message
        if not message.tool_calls:
            status, summary = "completed", (message.content or "").strip()
            break

        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in message.tool_calls
            ],
        })

        try:
            with get_cursor(commit=True) as cur:
                # keep the row current so a polling client sees progress as it happens
                cur.execute(
                    "UPDATE tailoring_runs SET steps_used = %s, heartbeat_at = now() WHERE id = %s",
                    (step, run_id),
                )
                for call in message.tool_calls:
                    outcome = execute_tool(cur, user_id, run_id, step, call)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(outcome),
                    })
        except Exception:
            # anything that isn't a grounding rejection aborts the transaction, taking the
            # tool_call row with it. End the run rather than leave it stuck at 'running'.
            logger.exception("tool execution failed run_id=%s step=%d", run_id, step)
            status, error_code = "failed", "tool_execution_failed"
            break

    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE tailoring_runs
            SET status = %s, steps_used = %s, input_tokens = %s, output_tokens = %s,
                error_code = %s, completed_at = now()
            WHERE id = %s
            """,
            (status, steps_used, input_tokens, output_tokens, error_code, run_id),
        )
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE tool_name = 'search_resume'),
                   (SELECT count(*) FROM proposed_edits WHERE run_id = %(run)s),
                   (SELECT count(*) FROM gaps WHERE run_id = %(run)s)
            FROM tool_calls WHERE run_id = %(run)s
            """,
            {"run": run_id},
        )
        searches, edits, gaps = cur.fetchone()
        counts = {"searches": searches, "edits": edits, "gaps": gaps}

    logger.info(
        "tailoring run=%s user=%s job=%s status=%s steps=%d searches=%d edits=%d gaps=%d "
        "prompt=%d completion=%d cost=$%.5f latency_ms=%.0f",
        run_id, user_id, job_id, status, steps_used,
        counts["searches"], counts["edits"], counts["gaps"],
        input_tokens, output_tokens, usd(input_tokens, output_tokens),
        (time.perf_counter() - t0) * 1000,
    )

    return {"run_id": str(run_id), "status": status, "steps_used": steps_used, "summary": summary}


def run_tailoring(get_cursor, user_id, job_id, max_steps=DEFAULT_MAX_STEPS):
    """Create and drive a run to completion, synchronously."""
    started = start_run(get_cursor, user_id, job_id, max_steps)
    if started is None or isinstance(started, dict):
        return started
    return execute_run(get_cursor, user_id, job_id, started, max_steps)


# one model call is capped at 60s, so a heartbeat this old means the worker is gone rather
# than busy — and it stays true with several workers, where "started long ago" does not
HEARTBEAT_TIMEOUT = "3 minutes"


def sweep_abandoned_runs(cur):
    """Close out every silent run, not just one being looked at.

    `reap_abandoned_run` only fires when someone opens that run's page, so a stranded run
    nobody revisits stays `running` forever. This is what a scheduled sweep calls.
    """
    cur.execute(
        """
        UPDATE tailoring_runs
        SET status = 'failed', error_code = 'abandoned', completed_at = now()
        WHERE status = 'running' AND heartbeat_at < now() - %s::interval
        RETURNING id
        """,
        (HEARTBEAT_TIMEOUT,),
    )
    return [str(row[0]) for row in cur.fetchall()]


def reap_abandoned_run(cur, user_id, run_id):
    """Close out a run whose worker stopped reporting, so the UI isn't polling a corpse."""
    cur.execute(
        """
        UPDATE tailoring_runs
        SET status = 'failed', error_code = 'abandoned', completed_at = now()
        WHERE id = %s AND user_id = %s AND status = 'running'
          AND heartbeat_at < now() - %s::interval
        """,
        (run_id, user_id, HEARTBEAT_TIMEOUT),
    )


def load_run(cur, user_id, run_id):
    """A run with everything it produced."""
    cur.execute(
        """
        SELECT id, job_id, status, model, max_steps, steps_used,
               input_tokens, output_tokens, error_code, started_at, completed_at
        FROM tailoring_runs
        WHERE id = %s AND user_id = %s
        """,
        (run_id, user_id),
    )
    row = cur.fetchone()
    if row is None:
        return None

    run = {
        "id": str(row[0]),
        "job_id": str(row[1]),
        "status": row[2],
        "model": row[3],
        "max_steps": row[4],
        "steps_used": row[5],
        "input_tokens": row[6],
        "output_tokens": row[7],
        "error_code": row[8],
        "started_at": row[9].isoformat(),
        "completed_at": row[10].isoformat() if row[10] else None,
    }

    cur.execute(
        """
        SELECT e.id, e.bullet_id, e.requirement, e.proposed_text, e.status,
               b.text,
               COALESCE(
                   json_agg(json_build_object('bullet_id', l.bullet_id, 'text', eb.text))
                   FILTER (WHERE l.id IS NOT NULL),
                   '[]'
               )
        FROM proposed_edits AS e
        LEFT JOIN resume_bullets AS b ON b.id = e.bullet_id
        LEFT JOIN evidence_links AS l ON l.edit_id = e.id
        LEFT JOIN resume_bullets AS eb ON eb.id = l.bullet_id
        WHERE e.run_id = %s AND e.user_id = %s
        GROUP BY e.id, b.text
        ORDER BY e.created_at
        """,
        (run_id, user_id),
    )
    run["edits"] = [
        {
            "id": str(r[0]),
            "bullet_id": str(r[1]) if r[1] else None,
            "requirement": r[2],
            "proposed_text": r[3],
            "status": r[4],
            "original_text": r[5],
            "evidence": [{"bullet_id": str(e["bullet_id"]), "text": e["text"]} for e in r[6]],
        }
        for r in cur.fetchall()
    ]

    cur.execute(
        "SELECT id, requirement, note, searched FROM gaps WHERE run_id = %s AND user_id = %s ORDER BY created_at",
        (run_id, user_id),
    )
    run["gaps"] = [
        {"id": str(r[0]), "requirement": r[1], "note": r[2], "searched": r[3]}
        for r in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT step_number, tool_name, arguments, status, error_message
        FROM tool_calls WHERE run_id = %s ORDER BY step_number, created_at
        """,
        (run_id,),
    )
    run["trace"] = [
        {"step": r[0], "tool": r[1], "arguments": r[2], "status": r[3], "error": r[4]}
        for r in cur.fetchall()
    ]

    return run
