"""Turning "I used this on that project" into a proposed edit, immediately.

The fit engine can tell that a skill is claimed in a keyword list and never demonstrated, or
that a gap is only a gap because the resume did not mention something. Both are the same
missing fact — which project it belongs to — and only the user has it.

Until now, answering that question recorded the fact and asked the user to start another run
to see anything come of it. This closes the loop: the answer arrives, and the edit it justifies
is proposed into the run they are already looking at.

Everything the agent has to satisfy, this satisfies too. The citation is a bullet retrieved
from the user's own evidence during this run; the skill is only claimable for bullets in the
entry they named; the user's own sentence is the evidence for whatever fact it adds; and the
same claim checker decides whether the rewrite stayed inside them. The user starting it changes
who chose the target, not what is allowed to be written.
"""

import json
import logging

from services.openai_services import surface_rewrite
from services.resume_evidence import add_entry_skill, entry_bullets
from services.skill_evidence import recompute_job_match
from services.tailoring_agent import GroundingError, tool_propose_edit
from services.usage import QuotaExceeded, budget

logger = logging.getLogger(__name__)

MAX_DETAIL_CHARS = 500
# a run still in flight has a worker writing to it; adding an edit underneath that is a race
# nobody asked for, and the agent may be about to rewrite the same bullet
OPEN_STATUSES = ("running", "waiting_for_user")


class SurfaceRefused(Exception):
    """The request was understood and cannot be satisfied. Carries a reason for the user."""


def _load_run(cur, user_id, run_id):
    cur.execute(
        "SELECT status, job_id FROM tailoring_runs WHERE id = %s AND user_id = %s",
        (run_id, user_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"status": row[0], "job_id": row[1]}


def _record_retrieval(cur, run_id, skill, bullets):
    """Write the retrieval down as the search it is.

    `verify_citation` requires a bullet to have been returned by a search in this run, and
    that rule is the point rather than a formality — it is what stops an id appearing from
    nowhere. So this performs a real retrieval against the user's evidence, scoped to the
    entry they named, and records it under the same tool name with its origin in the
    arguments, so a trace never implies the model went looking.
    """
    cur.execute("SELECT coalesce(max(step_number), 0) + 1 FROM tool_calls WHERE run_id = %s", (run_id,))
    step = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO tool_calls (run_id, step_number, call_id, tool_name, arguments, result, status)
        VALUES (%s, %s, %s, 'search_resume', %s, %s, 'completed')
        ON CONFLICT (run_id, call_id) DO NOTHING
        """,
        (
            run_id, min(step, 20), f"surface-{skill}-{step}",
            json.dumps({"query": skill, "source": "the entry the user named"}),
            json.dumps({"query": skill, "results": bullets, "count": len(bullets)}),
        ),
    )


def _record_answer(cur, user_id, run_id, bullet_id, skill, detail):
    """Store the user's sentence as an answered question.

    This is what makes the fact usable: `_claim_evidence` reads answered details for the same
    requirement, so a number or a tool named in the answer becomes supported evidence — and
    nothing else does.
    """
    question = f"What did you do with {skill} on this?"
    cur.execute(
        """
        INSERT INTO tailoring_detail_requests (
            run_id, user_id, bullet_id, requirement, question, answer, status, resolved_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'answered', now())
        ON CONFLICT (run_id, bullet_id, question) DO UPDATE
           SET answer = EXCLUDED.answer, status = 'answered', resolved_at = now()
        RETURNING id
        """,
        (run_id, user_id, bullet_id, skill, question, detail),
    )
    return cur.fetchone()[0]


def surface_skill(cur, user_id, run_id, skill, entry_id, detail):
    """Attach a skill to an entry and propose the edit that shows it.

    Returns the new edit. Raises SurfaceRefused with something worth reading when the claim
    cannot be supported — which is a real answer, not a failure: it means the sentence the
    user gave does not carry the fact the rewrite would need.
    """
    skill = (skill or "").strip()
    detail = (detail or "").strip()
    if not skill:
        raise SurfaceRefused("name the skill you used")
    if not detail:
        raise SurfaceRefused("say what you actually did with it — a bare yes cannot support a claim")
    if len(detail) > MAX_DETAIL_CHARS:
        raise SurfaceRefused(f"keep it to {MAX_DETAIL_CHARS} characters or fewer")

    run = _load_run(cur, user_id, run_id)
    if run is None:
        return None
    if run["status"] in OPEN_STATUSES:
        raise SurfaceRefused("this run is still working — wait for it to finish, then try again")

    bullets = entry_bullets(cur, user_id, entry_id)
    if not bullets:
        raise SurfaceRefused("that entry has no bullets to add this to")

    # the affirmation first: it is true whether or not the rewrite below succeeds, and it is
    # what authorises the skill's name for these bullets and no others
    add_entry_skill(cur, user_id, skill, entry_id)
    _record_retrieval(cur, run_id, skill, bullets)

    try:
        choice = surface_rewrite(
            skill=skill, detail=detail, bullets=[b["text"] for b in bullets],
            budget=budget(user_id, "surface_skill", run_id=run_id),
        )
    except QuotaExceeded:
        raise
    except Exception as exc:
        logger.exception("could not draft a surfacing rewrite for run_id=%s", run_id)
        raise SurfaceRefused("could not draft a rewrite just now — try again in a moment") from exc

    index = choice.get("bullet_index")
    if not isinstance(index, int) or not 0 <= index < len(bullets):
        # the model never sees or returns an id; an index out of range is simply unusable
        raise SurfaceRefused("could not pick a bullet to add this to")
    target = bullets[index]

    _record_answer(cur, user_id, run_id, target["bullet_id"], skill, detail)

    try:
        result = tool_propose_edit(cur, user_id, run_id, {
            "requirement": skill,
            "bullet_id": target["bullet_id"],
            "proposed_text": choice.get("proposed_text", ""),
            "evidence_bullet_ids": [target["bullet_id"]],
            "reason": f"You said you used {skill} on this project, so the bullet can show it.",
        }, surfacing=skill)
    except GroundingError as exc:
        # The checks that refuse the agent refuse this too. Said plainly, because the usual
        # cause is an answer that does not actually contain the fact the rewrite needs.
        raise SurfaceRefused(str(exc)) from None

    # Rescore the job now that this skill has evidence behind it. The run page reads gaps and
    # "claimed, but not shown" from the stored assessment, so without this the item the user
    # just answered stays on screen offering to ask them again.
    recompute_job_match(cur, user_id, run["job_id"])

    return {"edit_id": result["edit_id"], "bullet_id": target["bullet_id"], "skill": skill}

