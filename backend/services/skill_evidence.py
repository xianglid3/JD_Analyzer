"""Match a job's requirements against a resume's evidence.

Each requirement gets a state rather than a yes/no:

    EXPLICIT   named outright
    INFERRED   a named technology implies it (Tailwind → CSS)
    PARTIAL    only the general skill shows up ("cloud" for an AWS requirement)
    NONE       nothing

Scores come from those states with fixed weights, so the number is reproducible and every
part of it can be traced back to a bullet.
"""

from services.match import ALIASES, normalize_skill
from services.resume_evidence import evidence_is_stale
from services.skill_graph import evidence_for, implied_by
from services.text_match import index as token_index, mentions

EXPLICIT = "EXPLICIT"
INFERRED = "INFERRED"
PARTIAL = "PARTIAL"
NONE = "NONE"

STATE_WEIGHTS = {EXPLICIT: 1.0, INFERRED: 0.8, PARTIAL: 0.4, NONE: 0.0}

# how much a requirement counts, from how the posting framed it
IMPORTANCE_WEIGHTS = {"required": 3, "preferred": 2, "nice_to_have": 1}
DEFAULT_IMPORTANCE = "required"

# capability is what they can do; keyword coverage is what an ATS sees
CAPABILITY_SHARE = 0.7


# canonical form → every spelling of it, so a resume writing "c++" still matches a
# requirement that normalizes to "cpp"
_SPELLINGS = {}
for _variant, _canonical in ALIASES.items():
    _SPELLINGS.setdefault(_canonical, set()).add(_variant)


def _spellings(term):
    return [term] + sorted(_SPELLINGS.get(term, ()))


def _bullet_index(bullets):
    """Each bullet tokenized once, plus the raw text for display."""
    indexed = []
    for bullet in bullets:
        text = bullet["text"] if isinstance(bullet, dict) else str(bullet)
        indexed.append({
            "bullet_id": bullet.get("bullet_id") if isinstance(bullet, dict) else None,
            "kind": bullet.get("kind") if isinstance(bullet, dict) else None,
            "text": text,
            "normalized": normalize_skill(text),
            "tokens": token_index(text),
        })
    return indexed


def _mentions(entry, term):
    if entry["normalized"] == term:
        return True
    return mentions(entry["tokens"], term)


def evaluate_requirement(requirement, bullets):
    """One requirement against the evidence: state, the bullets behind it, and what it was
    inferred from."""
    term = normalize_skill(requirement)
    index = _bullet_index(bullets)

    explicit = [e for e in index if any(_mentions(e, spelling) for spelling in _spellings(term))]
    if explicit:
        return {
            "requirement": requirement,
            "state": EXPLICIT,
            "evidence": explicit[:3],
            "inferred_from": [],
        }

    for specific in evidence_for(term):
        hits = [e for e in index if any(_mentions(e, s) for s in _spellings(specific))]
        if hits:
            return {
                "requirement": requirement,
                "state": INFERRED,
                "evidence": hits[:3],
                "inferred_from": [specific],
            }

    # the reverse direction, allowed only here: they show the general skill where the job
    # wants a specific one. Weak on purpose — it never counts as matched.
    for general in implied_by(term):
        hits = [e for e in index if _mentions(e, general)]
        if hits:
            return {
                "requirement": requirement,
                "state": PARTIAL,
                "evidence": hits[:3],
                "inferred_from": [general],
            }

    return {"requirement": requirement, "state": NONE, "evidence": [], "inferred_from": []}


def evaluate_requirements(requirements, bullets):
    """Every requirement plus the three scores. Items can be strings or
    {"skill", "importance"}."""
    if not requirements:
        return None

    results = []
    for item in requirements:
        if isinstance(item, dict):
            name = item.get("skill") or item.get("requirement") or ""
            importance = item.get("importance", DEFAULT_IMPORTANCE)
        else:
            name, importance = str(item), DEFAULT_IMPORTANCE
        if not name.strip():
            continue
        if importance not in IMPORTANCE_WEIGHTS:
            importance = DEFAULT_IMPORTANCE

        evaluated = evaluate_requirement(name.strip(), bullets)
        evaluated["importance"] = importance
        results.append(evaluated)

    if not results:
        return None

    total_weight = sum(IMPORTANCE_WEIGHTS[r["importance"]] for r in results)
    capability = sum(
        STATE_WEIGHTS[r["state"]] * IMPORTANCE_WEIGHTS[r["importance"]] for r in results
    ) / total_weight
    # only a literal appearance counts here, however good the inferred evidence is
    keyword = sum(
        IMPORTANCE_WEIGHTS[r["importance"]] for r in results if r["state"] == EXPLICIT
    ) / total_weight
    overall = CAPABILITY_SHARE * capability + (1 - CAPABILITY_SHARE) * keyword

    return {
        "score": round(overall * 100, 2),
        "capability_score": round(capability * 100, 2),
        "keyword_score": round(keyword * 100, 2),
        "requirements": [
            {
                "requirement": r["requirement"],
                "state": r["state"],
                "importance": r["importance"],
                "inferred_from": r["inferred_from"],
                "evidence": [
                    {"bullet_id": e["bullet_id"], "text": e["text"]} for e in r["evidence"]
                ],
            }
            for r in results
        ],
        "matched": [r["requirement"] for r in results if r["state"] in (EXPLICIT, INFERRED)],
        "missing": [r["requirement"] for r in results if r["state"] == NONE],
    }


def load_evidence_bullets(cur, user_id):
    """Everything a requirement can match against: structured bullets, plus the plain skills
    list from the resume page so people who never built structured evidence still score.
    Only the structured ones carry ids, so only they can be cited.

    Stale evidence is left out entirely — scoring against a resume the user has replaced is
    the same lie as tailoring from it."""
    evidence = []
    if not evidence_is_stale(cur, user_id):
        cur.execute(
            """
            SELECT b.id, b.text, e.kind
            FROM resume_bullets AS b
            JOIN resume_entries AS e ON e.id = b.entry_id
            WHERE b.user_id = %s
            ORDER BY b.sort_order
            """,
            (user_id,),
        )
        evidence = [{"bullet_id": str(r[0]), "text": r[1], "kind": r[2]} for r in cur.fetchall()]

    cur.execute("SELECT skills FROM resumes WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    for skill in (row[0] if row and row[0] else []):
        evidence.append({"bullet_id": None, "text": skill, "kind": "skill"})

    return evidence


def requirements_for_job(requirements, skills):
    """Stored requirements if present, else the flat skills list — older jobs have no
    requirements and shouldn't drop to zero."""
    if requirements:
        return requirements
    return list(skills or [])


def match_for_job(cur, user_id, requirements, skills):
    """Score one job against the user's current evidence. None when there is nothing to score."""
    items = requirements_for_job(requirements, skills)
    if not items:
        return None
    return evaluate_requirements(items, load_evidence_bullets(cur, user_id))


def recompute_user_matches(cur, user_id):
    """Rescore every job after the resume changes: one read, one batched write."""
    import json

    bullets = load_evidence_bullets(cur, user_id)
    cur.execute("SELECT id, requirements, skills FROM jobs WHERE user_id = %s", (user_id,))
    rows = cur.fetchall()

    updates = []
    for job_id, requirements, skills in rows:
        items = requirements_for_job(requirements, skills)
        result = evaluate_requirements(items, bullets) if items else None
        updates.append({
            "id": str(job_id),
            "match_score": result["score"] if result else None,
            "match_detail": detail_for(result),
        })

    if updates:
        cur.execute(
            """
            UPDATE jobs AS job
            SET match_score = match.match_score,
                match_detail = match.match_detail
            FROM jsonb_to_recordset(%s::jsonb) AS match(
                id uuid,
                match_score numeric,
                match_detail jsonb
            )
            WHERE job.id = match.id AND job.user_id = %s
            """,
            (json.dumps(updates), user_id),
        )

    return len(updates)


def detail_for(result):
    """What lands in jobs.match_detail — the explanation, not just the number."""
    if not result:
        return None
    return {
        "matched": result["matched"],
        "missing": result["missing"],
        "capability_score": result["capability_score"],
        "keyword_score": result["keyword_score"],
        "requirements": result["requirements"],
    }
