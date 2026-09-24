"""Selector for question candidates produced by ``bullet_review_v2``.

The reviewer finds possible recruiter doubts one bullet at a time. This module sees those
candidates together and chooses the smallest useful, non-repetitive set. It cannot write or edit a
question: the server resolves every selected opaque id back to the exact input candidate.

Production calls it only for runs pinned to the V2 review contract. The isolated eval remains the
gate for judging its model output before enabling that contract in a deployment.
"""

from collections import Counter
import json
import logging
import os
import re

from services.bullet_review import ReviewUnavailable, squash

logger = logging.getLogger(__name__)

MODEL = os.environ.get("TAILORING_REVIEW_MODEL", "gpt-4o-mini")
MAX_SELECTED_PER_BULLET = 10
REJECTION_REASONS = (
    "duplicate",
    "already_answered",
    "low_value",
    "irrelevant",
    "unsupported_premise",
    "lower_priority_same_gap",
)


class ContractViolation(ReviewUnavailable):
    """Readable coordinator output that cannot safely be interpreted."""


DIRECT_OWNERSHIP = re.compile(
    r"^(?:built|designed|implemented|created|developed|architected|led|owned)\b",
    re.IGNORECASE,
)
BROAD_OWNERSHIP_QUESTION = re.compile(
    r"\b(?:what specific (?:features?|components?|parts?)|which parts?)\b.*"
    r"\b(?:personally )?(?:implement|implemented|build|built|develop|developed|design|designed)\b",
    re.IGNORECASE,
)
CONCRETE_SECOND_CLAUSE = re.compile(
    r"[:;]|\bso (?:that )?|\b(?:resulting|leading|preventing|prevented|reducing|reduced)\b",
    re.IGNORECASE,
)


def automatic_rejection_reason(candidate):
    """Return a provable selection-policy rejection, or None.

    This deliberately covers only the repeated production failure: a broad contribution question
    that asks the candidate to restate ownership already asserted by a concrete, multi-clause
    bullet. A short claim such as "Built an app" remains eligible because it supplies no mechanism
    or result; "worked on" remains eligible because it does not establish personal ownership.
    """
    if candidate.get("recruiter_doubt_type") != "contribution":
        return None
    bullet = _text(candidate.get("target_bullet"))
    question = _text(candidate.get("question"))
    if (
        DIRECT_OWNERSHIP.search(bullet)
        and CONCRETE_SECOND_CLAUSE.search(bullet)
        and BROAD_OWNERSHIP_QUESTION.search(question)
    ):
        return "low_value"
    return None


SYSTEM_PROMPT = """You are the Question Coordinator for a resume-tailoring system.

The recruiter reviewer already generated question candidates one bullet at a time. Your job is to
compare those candidates across the whole resume and select the smallest set that captures every
distinct, worthwhile missing fact.

You may SELECT or REJECT candidates. You may not invent, rewrite, combine or improve question
text. Return ids only. The server will look up the exact original question for every selected id.

SELECTION ORDER

1. Decide whether the missing fact would materially improve its target bullet.
2. Remove questions already answered by facts supplied for that same bullet.
3. Remove semantic duplicates and paraphrases. Preserve one clear representative.
4. Preserve complementary questions whose answers would add different facts.
5. Use job relevance and priority only after usefulness and distinctness are established.

RULES

- Select zero when none is worth the candidate's time.
- A ceiling is not a target. Never keep filler to approach it.
- Never select more than 10 questions for one bullet.
- Similar wording on different bullets is not automatically duplicate: separate work may require
  separate answers.
- Questions on sibling bullets are duplicates only when their context shows they investigate the
  same work and would likely receive the same answer.
- A high priority label cannot rescue a generic, redundant or unsupported question.
- Fit makes a useful question more relevant; it does not make a weak question useful.
- A resume-wide gap never belongs to an arbitrary bullet.
- Do not infer facts from the job description.
- Account for every supplied id exactly once: selected or rejected.

OWNERSHIP QUESTIONS

Resume bullets omit "I". A leading action verb is already a claim about the candidate. Reject a
broad contribution question as low_value when it merely asks what they personally built inside
work the target already says they Built, Designed, Implemented, Created, Developed, Architected,
Led or Owned AND the bullet names a concrete mechanism, scope or result. The answer might add more
detail, but that possibility alone does not justify taking the candidate's time.

- "Built an LLM platform with server-enforced grounding: unsupported edits are rejected" already
  states ownership and a mechanism. Reject "What features did you personally implement?"
- "Implemented reserve-before-spend idempotency so duplicate requests cannot trigger duplicate
  LLM calls, with response replay" already states ownership, mechanism and result. Reject "What
  part did you personally implement?"
- "Worked on the PostgreSQL backend using psycopg2, search, locking and indexes" names exposure
  but no owned action. Keep a focused question asking what database functionality they personally
  built or changed.

This rule is about redundancy, not verb matching alone. "Built an app" can still be too vague;
the concrete mechanism, scope or result is what makes the broad ownership follow-up low value.

Allowed rejection reasons:

- duplicate — equivalent to another selected question; name it in duplicate_of.
- already_answered — supplied answers already contain the missing fact.
- low_value — an answer would not materially improve the bullet.
- irrelevant — the weakness is real but not worth asking for this job.
- unsupported_premise — the question assumes something its bullet and answers do not establish.
- lower_priority_same_gap — it concerns the same missing fact as a better selected question; name
  that selected question in duplicate_of.

OUTPUT

Return only this JSON object:

{"selected_ids": ["<supplied id>"], "rejected": [
  {"id": "<supplied id>", "reason": "<allowed reason>",
   "duplicate_of": "<selected id, only for duplicate/lower_priority_same_gap; otherwise null>"}
]}
"""


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def candidate_id(bullet_id, local_id):
    """Stable opaque id for one review candidate."""
    bullet = _text(str(bullet_id))
    local = _text(str(local_id))
    if not bullet or not local:
        raise ValueError("question candidates need a bullet id and local id")
    return f"{bullet}:{local}"


def collect_candidates(bullets):
    """Flatten validated reviewer output without changing any question text.

    ``bullets`` is deliberately a small boundary object rather than a production review row. That
    keeps Stage 2 independently measurable and makes every fact the coordinator sees explicit.
    """
    collected, seen = [], set()
    for bullet_position, bullet in enumerate(bullets or []):
        bullet_id = _text(str(bullet.get("bullet_id") or ""))
        if not bullet_id:
            raise ValueError("every coordinator bullet needs bullet_id")
        for local_position, candidate in enumerate(bullet.get("question_candidates") or []):
            if not isinstance(candidate, dict):
                raise ValueError("question_candidates must contain objects")
            opaque_id = candidate_id(bullet_id, candidate.get("id") or "")
            if opaque_id in seen:
                raise ValueError(f"duplicate coordinator candidate id: {opaque_id}")
            seen.add(opaque_id)
            collected.append({
                "id": opaque_id,
                "bullet_id": bullet_id,
                "bullet_position": bullet_position,
                "candidate_position": local_position,
                "entry": _text(bullet.get("entry")),
                "target_bullet": _text(bullet.get("text")),
                "siblings": [
                    _text(item) for item in bullet.get("siblings") or [] if _text(item)
                ],
                "answers_given_in_this_run": [
                    _text(item) for item in bullet.get("answers") or [] if _text(item)
                ],
                "question": _text(candidate.get("question")),
                "missing_fact": _text(candidate.get("missing_fact")),
                "recruiter_doubt_type": _text(candidate.get("recruiter_doubt_type")),
                "why_it_matters_for_this_job": _text(
                    candidate.get("why_it_matters_for_this_job")
                ),
                "expected_resume_change": _text(candidate.get("expected_resume_change")),
                "priority": _text(candidate.get("priority")),
                "requirement_reference": candidate.get("requirement_reference"),
            })
    return collected


def payload(job, candidates):
    title, company, summary, skills = job
    return {
        "job_description": {
            "title": title,
            "company": company,
            "summary": summary,
            "requirements": list(skills or []),
        },
        "question_candidates": [dict(candidate) for candidate in candidates],
    }


def _index(candidates):
    by_id = {}
    for candidate in candidates or []:
        if not isinstance(candidate, dict) or not _text(candidate.get("id")):
            raise ValueError("every coordinator candidate needs an id")
        key = _text(candidate["id"])
        if key in by_id:
            raise ValueError(f"duplicate coordinator candidate id: {key}")
        by_id[key] = candidate
    return by_id


def validate(raw, candidates):
    """Validate a complete selection and resolve ids to immutable input candidates."""
    if not isinstance(raw, dict):
        raise ContractViolation("the coordinator response must be an object")
    by_id = _index(candidates)
    selected_raw = raw.get("selected_ids")
    rejected_raw = raw.get("rejected")
    if not isinstance(selected_raw, list) or not isinstance(rejected_raw, list):
        raise ContractViolation("selected_ids and rejected must both be arrays")

    selected_ids = []
    for value in selected_raw:
        key = _text(value)
        if not key or key not in by_id:
            raise ContractViolation(f"unknown selected id: {value!r}")
        if key in selected_ids:
            raise ContractViolation(f"selected id appears twice: {key}")
        selected_ids.append(key)

    counts = Counter(str(by_id[key].get("bullet_id") or "") for key in selected_ids)
    over = [bullet for bullet, count in counts.items() if count > MAX_SELECTED_PER_BULLET]
    if over:
        raise ContractViolation(
            f"more than {MAX_SELECTED_PER_BULLET} questions selected for bullet {over[0]}"
        )

    # Exact duplicate text is the one semantic-looking condition the server can prove. It says
    # nothing about paraphrases, which remain the coordinator's judgment.
    normalized = {}
    for key in selected_ids:
        question = squash(by_id[key].get("question") or "")
        duplicate_key = (str(by_id[key].get("bullet_id") or ""), question)
        if question and duplicate_key in normalized:
            raise ContractViolation(
                f"selected exact duplicate questions: {normalized[duplicate_key]} and {key}"
            )
        normalized[duplicate_key] = key

    rejections, rejected_ids, ignored_rejections = [], set(), []
    for item in rejected_raw:
        if not isinstance(item, dict):
            raise ContractViolation("every rejection must be an object")
        key = _text(item.get("id"))
        reason = _text(item.get("reason"))
        duplicate_of = _text(item.get("duplicate_of")) or None
        if key not in by_id:
            # An extra rejection cannot authorize a question or alter a supplied candidate. The
            # model occasionally invents a sibling id such as ``...:c2`` while still accounting
            # for every real input. Record and ignore that harmless noise. Unknown *selected*
            # ids remain a hard failure above because those would create user-visible work.
            ignored_rejections.append({"id": key or None, "reason": reason or None})
            continue
        if key in selected_ids:
            raise ContractViolation(f"id is both selected and rejected: {key}")
        if key in rejected_ids:
            raise ContractViolation(f"rejected id appears twice: {key}")
        if reason not in REJECTION_REASONS:
            raise ContractViolation(f"invalid rejection reason for {key}: {reason or None!r}")
        needs_representative = reason in ("duplicate", "lower_priority_same_gap")
        if needs_representative:
            if duplicate_of not in selected_ids or duplicate_of == key:
                raise ContractViolation(
                    f"{reason} rejection {key} must name a selected representative"
                )
        elif duplicate_of is not None:
            raise ContractViolation(
                f"rejection {key} may not set duplicate_of for reason {reason}"
            )
        rejected_ids.add(key)
        rejections.append({"id": key, "reason": reason, "duplicate_of": duplicate_of})

    accounted = set(selected_ids) | rejected_ids
    missing = set(by_id) - accounted
    if missing:
        raise ContractViolation(
            "the coordinator did not account for: " + ", ".join(sorted(missing))
        )

    return {
        "selected_ids": selected_ids,
        # Resolve server-side: callers never trust model-supplied question text.
        "selected": [dict(by_id[key]) for key in selected_ids],
        "rejected": rejections,
        "ignored_rejections": ignored_rejections,
    }


def evaluation_problems(result, expected):
    """Machine-check one isolated coordinator case without prescribing exact prose."""
    if not isinstance(expected, dict):
        return ["the case has no structured expected result"]
    if not isinstance(result, dict):
        return ["the coordinator result is unavailable"]

    problems = []
    selected = result.get("selected_ids") or []
    selected_set = set(selected)
    bounds = expected.get("selected") or {}
    minimum = bounds.get("min", 0)
    maximum = bounds.get("max", 10_000)
    if len(selected) < minimum or len(selected) > maximum:
        problems.append(
            f"selected {len(selected)} questions; expected between {minimum} and {maximum}"
        )

    for key in expected.get("required_selected_ids") or []:
        if key not in selected_set:
            problems.append(f"required question was not selected: {key}")
    for key in expected.get("forbidden_selected_ids") or []:
        if key in selected_set:
            problems.append(f"forbidden question was selected: {key}")
    for alternatives in expected.get("required_selected_groups") or []:
        if not any(key in selected_set for key in alternatives):
            problems.append(
                "none of the acceptable questions was selected: " + ", ".join(alternatives)
            )

    rejected = {item.get("id"): item for item in result.get("rejected") or []}
    for key, reason in (expected.get("required_rejections") or {}).items():
        if key not in rejected:
            problems.append(f"required rejection is missing: {key}")
        elif rejected[key].get("reason") not in (
            reason if isinstance(reason, list) else [reason]
        ):
            problems.append(
                f"{key} was rejected as {rejected[key].get('reason')}, expected {reason}"
            )
    for group in expected.get("required_rejection_groups") or []:
        ids = group.get("ids") or []
        reasons = group.get("reasons") or []
        if not any(
            key in rejected and rejected[key].get("reason") in reasons
            for key in ids
        ):
            problems.append(
                "no expected rejection found for " + ", ".join(ids)
                + " with reason " + " | ".join(reasons)
            )
    reason_counts = Counter(item.get("reason") for item in result.get("rejected") or [])
    for reason, bounds in (expected.get("rejection_reason_counts") or {}).items():
        count = reason_counts[reason]
        minimum = bounds.get("min", 0)
        maximum = bounds.get("max", 10_000)
        if count < minimum or count > maximum:
            problems.append(
                f"rejected {count} as {reason}; expected between {minimum} and {maximum}"
            )
    return problems


def request_selection(job, candidates, budget=None, model=None):
    from services.openai_services import complete_json

    automatic = []
    eligible = []
    for candidate in candidates:
        reason = automatic_rejection_reason(candidate)
        if reason:
            automatic.append({"id": candidate["id"], "reason": reason, "duplicate_of": None})
        else:
            eligible.append(candidate)

    if not eligible:
        return {"selected_ids": [], "rejected": automatic}

    response = complete_json(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload(job, eligible))},
        ],
        model=model or MODEL,
        budget=budget,
        kind="tailoring_review",
        timeout=60,
    )
    content = response.choices[0].message.content or "{}"
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("question coordinator response was not valid JSON (%d chars): %s",
                       len(content), content[-120:])
        raise ReviewUnavailable(
            f"the coordinator response was not valid JSON ({len(content)} chars)"
        ) from exc
    rejected = result.get("rejected")
    if isinstance(rejected, list):
        result["rejected"] = [*rejected, *automatic]
    return result


def coordinate(job, bullets, budget=None, model=None):
    """Collect candidates, make one selection call, and return immutable selected questions."""
    candidates = collect_candidates(bullets)
    if not candidates:
        return {"selected_ids": [], "selected": [], "rejected": []}
    return validate(
        request_selection(job, candidates, budget=budget, model=model),
        candidates,
    )
