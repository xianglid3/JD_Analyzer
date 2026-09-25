"""Selector for question candidates produced by ``bullet_review_v2``.

The reviewer finds possible recruiter doubts one bullet at a time. This module sees those
candidates together and chooses the smallest useful, non-repetitive set. It cannot write or edit a
question: the server resolves every selected opaque id back to the exact input candidate.

Production calls it only for runs pinned to the V2 review contract. The isolated eval remains the
gate for judging its model output before enabling that contract in a deployment.
"""

from collections import Counter
from copy import deepcopy
import json
import logging
import os
import re
import time

from services.bullet_review import ReviewUnavailable, squash
from services.claim_check import states_a_result

logger = logging.getLogger(__name__)

MODEL = os.environ.get("TAILORING_REVIEW_MODEL", "gpt-4o-mini")
MAX_SELECTED_PER_BULLET = 10
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
REJECTION_REASONS = (
    "duplicate",
    "already_answered",
    "low_value",
    "irrelevant",
    "unsupported_premise",
    "lower_priority_same_gap",
    # Server-only fallback after the model omits an id twice. It is intentionally absent from the
    # prompt's reason list: the coordinator should still make an explicit decision when it can.
    "not_selected",
)


class ContractViolation(ReviewUnavailable):
    """Readable coordinator output that cannot safely be interpreted."""


OBSERVATION_QUESTION = re.compile(
    r"\b(?:what did you observe|what showed|how did you know|what evidence)\b",
    re.IGNORECASE,
)


def automatically_preserve(candidate):
    """Return a lexical observation hint, never authority to select a question.

    If the target itself claims improvement/reduction/etc., asking what was observed is factual
    validation of an existing claim. It is not the generic impact fishing this system rejects.
    Low-priority questions remain coordinator judgment, as do bullets that claim no result.
    """
    return bool(
        candidate.get("recruiter_doubt_type") == "result_validation"
        and candidate.get("priority") in ("high", "medium")
        and states_a_result(_text(candidate.get("target_bullet")))
        and OBSERVATION_QUESTION.search(_text(candidate.get("question")))
    )


SYSTEM_PROMPT = """You are the Question Coordinator for a resume-tailoring system.

Focused reviewers identified evidence-backed missing facts and a question generator proposed
candidate questions. Your job is to
compare those candidates across the whole resume and select the smallest set that captures every
distinct, worthwhile missing fact.

Each candidate may include a source_quote and a missing_fact created by an evidence-only audit.
Treat those as the fixed reason the question exists. Reject a question as low_value when it does
not actually seek that missing fact. Never reinterpret a job requirement as a replacement gap.
You select among supplied questions; you do not compensate for a rejected question by inventing
another one.

Review findings, uncertainty links, siblings and answers are supplied for context. Verify them
against the source: a reviewer may misread already-stated work as missing. Wording hints are
fallible heuristics, not rules. Judge what the complete evidence establishes, without assigning
ownership from a verb list. A result-validation question is redundant if the source already
states the observation.

Apply a POLISHED-BULLET VETO before selecting. When the target already states a concrete action or
change, its object or system scope, and a useful differentiator such as a mechanism, boundary,
behavior, verification method, or result, reject questions that merely request deeper detail about
those same facts. The applicant's time is justified only when the answer would add or replace a
concrete resume clause and materially change recruiter understanding. A question suitable for a
technical interview is not automatically useful for resume editing.

Examples: reject requests for the functionality of already listed PostgreSQL features, the behavior
of already named worker recovery mechanisms, more architecture detail about already specified
Docker services, per-fault behavior for an already concrete and tested motor controller, or the
benefits of already named cryptographic operations. Preserve a question for "worked on database
functionality" or "improved background processing" because those lack the concrete change. Preserve
a question about a ROS or point-cloud bullet that names an area but not the concrete contribution.
You may SELECT or REJECT candidates. You may not invent, rewrite, combine or improve question
text. Return ids only. The server will look up the exact original question for every selected id.

SELECTION ORDER

1. Decide whether the missing fact would materially improve its target bullet.
2. Remove questions already answered by facts supplied for that same bullet.
3. Remove semantic duplicates and paraphrases. Preserve one clear representative.
4. After rejecting an overlap, inspect the remaining candidates for that bullet and select the
   next useful one when it seeks a different fact and would create a different resume change.
5. Preserve complementary questions whose answers would add different facts.
6. Use job relevance and priority only after usefulness and distinctness are established.

A generic result, benefit, metric, or validation question never substitutes for a concrete clarity
question about what was implemented, changed, stored, enforced, or prevented. When the target is
materially vague and a clarity candidate would add that missing mechanism or boundary, preserve it
unless the supplied evidence already answers it or the question has an unsupported premise. A
result-validation candidate may also survive when it seeks a genuinely different useful fact.

RULES

- Select zero when none is worth the candidate's time.
- A ceiling is not a target. Never keep filler to approach it.
- Never select more than 10 questions for one bullet.
- Similar wording on different bullets is not automatically duplicate: separate work may require
  separate answers.
- Questions on sibling bullets are duplicates only when their context shows they investigate the
  same work and would likely receive the same answer.
- A project-wide contribution question and a concrete subsystem contribution question in the same
  entry may seek the same answer. When the broad bullet says it focuses on that subsystem and the
  sibling names the subsystem work, keep the more concrete subsystem question and reject the broad
  one as `lower_priority_same_gap`. This is nested work, not merely similar wording.
- Compare candidates within each entry as a set. Two selected questions in one project must have
  distinct `missing_fact` and `expected_resume_change` meanings, not merely different wording.
- Rejecting one bullet's top candidate does not automatically silence that bullet. Consider its
  remaining candidates in order and select a non-overlapping replacement when one is worthwhile.
- Never select a replacement merely to maintain a question count. Fewer questions is correct when
  every remaining alternative is redundant or weak.
- A high priority label cannot rescue a generic, redundant or unsupported question.
- Fit makes a useful question more relevant; it does not make a weak question useful.
- A resume-wide gap never belongs to an arbitrary bullet.
- Do not infer facts from the job description.
- Account for every supplied id exactly once with one decision row. Never emit two rows for the
  same id.

NESTED SIBLING EXAMPLE

One project has these candidates:

- broad contribution: "What did you contribute to the autonomous-driving stack?"
- concrete contribution: "What did you implement in point-cloud filtering and ground removal?"
- system scope: "What role did the perception software play in the autonomous-driving stack?"

If the broad bullet says it focused on perception and the sibling bullet describes the point-cloud
work, the broad and concrete contribution questions would likely receive the same implementation
answer. Select the concrete contribution question, reject the broad contribution question as
`lower_priority_same_gap` pointing to it, and select the system-scope question because its answer
would add a different fact. Do not apply this example when sibling bullets describe genuinely
separate components whose ownership answers would differ.

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

{"decisions": [
  {"id": "<supplied id>", "action": "select", "reason": null,
   "duplicate_of": null},
  {"id": "<supplied id>", "action": "reject", "reason": "<allowed reason>",
   "duplicate_of": "<selected id, only for duplicate/lower_priority_same_gap; otherwise null>"}
]}

There must be exactly one decision row for every supplied id. `reason` and `duplicate_of` must be
null for a selected id. A rejected id must use an allowed reason.
"""


OVERLAP_PROMPT = """You compare proposed resume questions within the same project or role.

Your only job is to identify questions that would probably receive substantially the same honest
answer. Do not judge whether a question is useful and do not write or rewrite question text.

- A broad project-contribution question and a concrete subsystem-contribution question overlap
  when the broad bullet says it focuses on that subsystem. Keep the concrete representative.
- Questions about different facts do not overlap merely because they discuss the same project.
  Ownership of a subsystem and that subsystem's role in the larger system are different facts.
- Separate components remain separate when their ownership answers would differ.
- For an overlap, point `duplicate_of` to the most specific, higher-priority representative.
- Return exactly one row for every supplied id. A representative or distinct question has
  `duplicate_of: null`. Never create chains: a representative must itself have null.

Return only this JSON object:

{"decisions": [
  {"id": "<supplied id>", "duplicate_of": "<representative id or null>"}
]}
"""


OVERLAP_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "duplicate_of": {"type": ["string", "null"]},
        },
        "required": ["id", "duplicate_of"],
        "additionalProperties": False,
    }}},
    "required": ["decisions"],
    "additionalProperties": False,
}

SELECTION_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "action": {"type": "string", "enum": ["select", "reject"]},
            "reason": {"type": ["string", "null"]},
            "duplicate_of": {"type": ["string", "null"]},
        },
        "required": ["id", "action", "reason", "duplicate_of"],
        "additionalProperties": False,
    }}},
    "required": ["decisions"],
    "additionalProperties": False,
}


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
                "review_findings": bullet.get("review_findings") or {},
                "fit_context": bullet.get("fit_context") or {},
                "uncertainty_id": candidate.get("uncertainty_id"),
                "source_quote": _text(candidate.get("source_quote")),
                "question": _text(candidate.get("question")),
                "missing_fact": _text(candidate.get("missing_fact")),
                "recruiter_doubt_type": _text(candidate.get("recruiter_doubt_type")),
                "why_it_matters_for_this_job": _text(
                    candidate.get("why_it_matters_for_this_job")
                ),
                "expected_resume_change": _text(candidate.get("expected_resume_change")),
                "priority": _text(candidate.get("priority")),
                "requirement_reference": candidate.get("requirement_reference"),
                "focused_candidate_id": candidate.get("focused_candidate_id"),
                "finding_ids": list(candidate.get("finding_ids") or []),
                "evidence_ids": list(candidate.get("evidence_ids") or []),
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
        "question_candidates": [{**candidate, "wording_hints": {
            "possible_result_observation": automatically_preserve(candidate),
        }} for candidate in candidates],
    }


def _overlap_subset(candidates):
    """Candidates worth a same-answer comparison.

    Comparing everything invites the model to group unrelated resume sections. Only candidates
    with the same doubt type on different bullets of the same named entry can overlap here.
    """
    grouped = {}
    for candidate in candidates:
        entry = _text(candidate.get("entry"))
        doubt_type = _text(candidate.get("recruiter_doubt_type"))
        if entry and doubt_type:
            grouped.setdefault((entry, doubt_type), []).append(candidate)
    ids = {
        item["id"]
        for group in grouped.values()
        if len({str(item.get("bullet_id")) for item in group}) > 1
        for item in group
    }
    return [candidate for candidate in candidates if candidate["id"] in ids]


def _normalize_overlap(raw, candidates):
    """Return overlap mapping plus conservative repairs for harmless model omissions."""
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), list):
        raise ContractViolation("overlap decisions must be an array")
    by_id = _index(candidates)
    decisions, repairs = {}, []
    for item in raw["decisions"]:
        if not isinstance(item, dict):
            raise ContractViolation("every overlap decision must be an object")
        key = _text(item.get("id"))
        duplicate_of = _text(item.get("duplicate_of")) or None
        if key not in by_id:
            # The overlap pass cannot authorize or select work. Ignoring an extra id is safe;
            # every real input id is still defaulted to distinct and judged by final selection.
            repairs.append(f"overlap {key or None}: ignored unknown id")
            continue
        if key in decisions:
            raise ContractViolation(f"overlap decision id appears twice: {key}")
        if duplicate_of is not None and duplicate_of not in by_id:
            raise ContractViolation(
                f"overlap decision {key} has an invalid representative: {duplicate_of!r}"
            )
        if duplicate_of == key:
            repairs.append(f"overlap {key}: treated self-reference as distinct")
            duplicate_of = None
        decisions[key] = duplicate_of
    missing = set(by_id) - set(decisions)
    for key in sorted(missing):
        # The overlap pass can only remove work. An omitted id therefore stays distinct and is
        # still judged by the complete selection pass; silently deleting it would lose recall.
        decisions[key] = None
        repairs.append(f"overlap {key}: omitted, treated as distinct")
    for key, representative in decisions.items():
        if representative and decisions.get(representative) is not None:
            raise ContractViolation(
                f"overlap representative {representative} must not point to another id"
            )

    # The model decides which answers overlap; the server orients each complete overlap group
    # toward one winner using the closed priority field. Normalizing the whole group (rather than
    # each edge independently) guarantees that every loser points directly to the winner.
    groups = {}
    for key, representative in decisions.items():
        if representative:
            groups.setdefault(representative, []).append(key)

    input_order = {key: position for position, key in enumerate(by_id)}
    normalized = {}
    for root, duplicates in groups.items():
        members = [root, *duplicates]
        winner = min(
            members,
            key=lambda candidate_id: (
                PRIORITY_RANK.get(by_id[candidate_id].get("priority"), 3),
                0 if candidate_id == root else 1,
                input_order[candidate_id],
            ),
        )
        for member in members:
            if member != winner:
                normalized[member] = winner
    return normalized, repairs


def validate_overlap(raw, candidates):
    """Return ``duplicate id -> representative id`` from overlap decisions."""
    return _normalize_overlap(raw, candidates)[0]


def _usage(response):
    usage = getattr(response, "usage", None)
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if usage is None:
        return None
    return {key: getattr(usage, key, None) for key in
            ("prompt_tokens", "completion_tokens", "total_tokens")}


def _trace_event(stage, scope_id, attempt, model, messages, input_payload):
    return {
        "contract": "focused_v1",
        "prompt_version": "question-coordinator-v2-2026-09-25b",
        "stage": stage,
        "scope_id": scope_id,
        "attempt": attempt,
        "model": model or MODEL,
        "input": input_payload,
        "messages": messages,
        "settings": {
            "kind": "tailoring_review", "timeout": 60,
            "response_format": "json_schema",
            "schema_name": f"focused_{stage}",
        },
    }


def request_overlap(candidates, budget=None, model=None, trace_callback=None,
                    trace_scope="resume"):
    """Run the focused same-answer judgment only when cross-bullet comparison is possible."""
    from services.openai_services import complete_json

    compared = _overlap_subset(candidates)
    if not compared:
        return {}
    messages = [
        {"role": "system", "content": OVERLAP_PROMPT},
        {"role": "user", "content": json.dumps({"question_candidates": compared})},
    ]
    failure = None
    input_payload = {"question_candidates": compared}
    for attempt_index in range(2):
        attempt = attempt_index + 1
        event = _trace_event("coordinator_overlap", trace_scope, attempt, model,
                             deepcopy(messages), input_payload)
        started = time.perf_counter()
        try:
            response = complete_json(
                messages,
                model=model or MODEL,
                budget=budget,
                kind="tailoring_review",
                timeout=60,
                schema=OVERLAP_SCHEMA if trace_callback else None,
                schema_name="focused_coordinator_overlap",
            )
            content = response.choices[0].message.content or "{}"
            event["raw_response"] = content
            event["usage"] = _usage(response)
            normalized, repairs = _normalize_overlap(json.loads(content), compared)
            event.update({"status": "completed", "normalized": normalized,
                          "validation": {"valid": True, "contract_repairs": repairs}})
            return normalized
        except json.JSONDecodeError as exc:
            failure = ReviewUnavailable(
                f"the overlap response was not valid JSON ({len(content)} chars)"
            )
            failure.__cause__ = exc
        except ContractViolation as exc:
            failure = exc
        except Exception as exc:
            failure = exc
        finally:
            event["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            if "status" not in event:
                event.update({"status": "failed", "error": str(failure),
                              "validation": {"valid": False,
                                             "error_type": type(failure).__name__}})
            if trace_callback:
                trace_callback(event)
        if event["status"] == "completed":
            return event["normalized"]
        if attempt_index == 0:
            messages.append({
                "role": "user",
                "content": (
                    "Your previous overlap result could not be used: " + str(failure) + ". "
                    "Return exactly one row per supplied id. Representatives must have null and "
                    "duplicates must point directly to a representative."
                ),
            })
    raise failure


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


def _selection_lists(raw, *, repair_conflicts=False):
    """Read the V2 decision rows or the legacy two-list shape.

    Stored coordinator calls created before the decision-row contract still contain
    ``selected_ids`` and ``rejected``. Keeping that shape readable lets old runs resume while new
    model output cannot put one id in two top-level lists.
    """
    decisions = raw.get("decisions")
    if decisions is None:
        selected_raw = raw.get("selected_ids")
        rejected_raw = raw.get("rejected")
        if not isinstance(selected_raw, list) or not isinstance(rejected_raw, list):
            raise ContractViolation(
                "decisions must be an array (or legacy selected_ids and rejected arrays)"
            )
        return selected_raw, rejected_raw, []
    if not isinstance(decisions, list):
        raise ContractViolation("decisions must be an array")

    selected_raw, rejected_raw, seen, contract_repairs = [], [], {}, []
    for item in decisions:
        if not isinstance(item, dict):
            raise ContractViolation("every decision must be an object")
        key = _text(item.get("id"))
        if not key:
            raise ContractViolation("every decision needs an id")
        action = _text(item.get("action"))
        reason = _text(item.get("reason")) or None
        duplicate_of = _text(item.get("duplicate_of")) or None
        normalized = (action, reason, duplicate_of)
        if key in seen:
            previous = seen[key]
            if repair_conflicts and previous == ("select", None, None) \
                    and action == "reject" \
                    and reason in ("duplicate", "lower_priority_same_gap") \
                    and duplicate_of == key:
                # The corrective response sometimes emits the valid selected row and then an
                # impossible self-duplicate rejection for the same id. The latter cannot express
                # a meaningful rejection: a question cannot be its own better representative.
                # Ignore only that closed contradiction after the normal retry, and audit it.
                contract_repairs.append(
                    f"decision {key}: ignored impossible self-{reason} rejection after select"
                )
                continue
            if repair_conflicts and normalized == previous:
                contract_repairs.append(
                    f"decision {key}: removed an exact duplicate row after correction"
                )
                continue
            raise ContractViolation(f"decision id appears twice: {key}")
        seen[key] = normalized
        if action == "select":
            if reason is not None or duplicate_of is not None:
                raise ContractViolation(
                    f"selected decision {key} may not set reason or duplicate_of"
                )
            selected_raw.append(key)
        elif action == "reject":
            rejected_raw.append({
                "id": key,
                "reason": reason,
                "duplicate_of": duplicate_of,
            })
        else:
            raise ContractViolation(f"invalid decision action for {key}: {action or None!r}")
    return selected_raw, rejected_raw, contract_repairs


def _merge_corrective_decisions(previous, correction):
    """Apply a corrective response that returned only changed or previously omitted rows.

    The correction prompt asks for the complete set, but GPT-4o-mini sometimes treats it as a
    patch. Rows in the correction replace prior rows with the same id; untouched prior rows remain.
    Validation still checks the merged result against every server-owned candidate, so this repair
    cannot invent an id, select omitted work, or bypass any selection invariant.
    """
    previous_rows = previous.get("decisions") if isinstance(previous, dict) else None
    correction_rows = correction.get("decisions") if isinstance(correction, dict) else None
    if not isinstance(previous_rows, list) or not isinstance(correction_rows, list):
        return correction, False
    replaced = {
        _text(row.get("id")) for row in correction_rows
        if isinstance(row, dict) and _text(row.get("id"))
    }
    merged = [
        row for row in previous_rows
        if not isinstance(row, dict) or _text(row.get("id")) not in replaced
    ]
    merged.extend(correction_rows)
    return {"decisions": merged}, True


def validate(
    raw,
    candidates,
    *,
    allow_missing=False,
    repair_invalid_representatives=False,
):
    """Validate a complete selection and resolve ids to immutable input candidates."""
    if not isinstance(raw, dict):
        raise ContractViolation("the coordinator response must be an object")
    by_id = _index(candidates)
    selected_raw, rejected_raw, selection_repairs = _selection_lists(
        raw, repair_conflicts=allow_missing,
    )

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
    contract_repairs = list(selection_repairs)
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
                if not repair_invalid_representatives:
                    raise ContractViolation(
                        f"{reason} rejection {key} must name a selected representative"
                    )
                # A corrective patch can refer to a representative that was never supplied or
                # was replaced by another decision. The rejection itself is conservative, but
                # preserving the false equivalence would corrupt the final decision graph. After
                # both model attempts and the prior/corrective rows have been merged, retain the
                # rejection as the honest technical fallback and remove only its stale edge.
                contract_repairs.append(
                    f"rejection {key}: replaced invalid {reason} representative "
                    f"{duplicate_of or None} with not_selected"
                )
                reason = "not_selected"
                duplicate_of = None
                needs_representative = False
            else:
                if by_id[key].get("recruiter_doubt_type") != by_id[duplicate_of].get(
                    "recruiter_doubt_type"
                ):
                    # The model still decided not to spend the candidate's time on this question;
                    # it merely used an impossible explanation. Keep the selection judgment,
                    # remove the false equivalence, and record the honest generic reason.
                    reason = "low_value"
                    duplicate_of = None
                    needs_representative = False
        elif duplicate_of is not None:
            # `duplicate_of` has no meaning for already_answered/low_value/etc., but the extra
            # pointer cannot select a question or change the rejection. Discard it and preserve
            # the repair instead of losing an otherwise complete conservative selection.
            contract_repairs.append(
                f"rejection {key}: removed duplicate_of from reason {reason}"
            )
            duplicate_of = None
        rejected_ids.add(key)
        rejections.append({"id": key, "reason": reason, "duplicate_of": duplicate_of})

    accounted = set(selected_ids) | rejected_ids
    missing = set(by_id) - accounted
    if missing:
        if not allow_missing:
            raise ContractViolation(
                "the coordinator did not account for: " + ", ".join(sorted(missing))
            )
        # One malformed row must not discard every valid selection after the corrective retry.
        # Asking less is the conservative fallback: omitted candidates never become visible work,
        # and the stored reason says exactly what happened rather than pretending they were weak.
        logger.warning(
            "coordinator omitted %d candidate(s) after correction: %s",
            len(missing), ",".join(sorted(missing)),
        )
        for key in by_id:
            if key in missing:
                rejected_ids.add(key)
                rejections.append({
                    "id": key,
                    "reason": "not_selected",
                    "duplicate_of": None,
                })

    return {
        "selected_ids": selected_ids,
        # Resolve server-side: callers never trust model-supplied question text.
        "selected": [dict(by_id[key]) for key in selected_ids],
        "rejected": rejections,
        "ignored_rejections": ignored_rejections,
        "contract_repairs": contract_repairs,
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


def combine_selection(candidates, chosen, overlaps):
    """Restore overlap losers after selecting among their representatives.

    This is pure so an interrupted eval can recover from captured provider responses without
    making another paid call. Production and recovery therefore use the same combination rules.
    """
    selected_set = {*chosen["selected_ids"]}
    # Stable resume/candidate order, independent of the model's array ordering.
    selected = [candidate["id"] for candidate in candidates
                if candidate["id"] in selected_set]
    selected_set = set(selected)
    rejections = list(chosen["rejected"])
    rejected_by_id = {item["id"]: item for item in rejections}
    for duplicate, representative in overlaps.items():
        if representative in selected_set:
            rejection = {
                "id": duplicate,
                "reason": "lower_priority_same_gap",
                "duplicate_of": representative,
            }
        else:
            # If the representative was itself rejected as not worth asking, its broader
            # duplicate is rejected for the same reason. A duplicate chain is flattened
            # to the selected final representative when one exists.
            representative_rejection = rejected_by_id.get(representative) or {}
            reason = representative_rejection.get("reason") or "low_value"
            final_representative = representative_rejection.get("duplicate_of")
            if reason in ("duplicate", "lower_priority_same_gap"):
                if final_representative not in selected_set:
                    reason, final_representative = "low_value", None
            else:
                final_representative = None
            rejection = {
                "id": duplicate,
                "reason": reason,
                "duplicate_of": final_representative,
            }
        rejections.append(rejection)
        rejected_by_id[duplicate] = rejection
    combined = {"selected_ids": selected, "rejected": rejections}
    repairs = chosen.get("contract_repairs") or []
    if repairs:
        combined["contract_repairs"] = list(repairs)
    validate(combined, candidates)
    return combined


def request_selection(job, candidates, budget=None, model=None, trace_callback=None,
                      trace_scope="resume", stage_cache=None):
    from services.openai_services import complete_json

    # Every candidate receives semantic review against its evidence. Wording heuristics
    # cannot establish that a question is already answered or necessarily worthwhile.
    eligible = list(candidates)
    if not eligible:
        return {"selected_ids": [], "rejected": []}

    # Deduplication is a separate, narrower judgment. The final selector repeatedly kept a broad
    # project ownership question beside a concrete subsystem ownership question even when asked
    # to compare everything at once. Removing same-answer alternatives first lets the selector
    # judge usefulness among genuinely different facts.
    cache = stage_cache or {}
    overlaps = cache.get((trace_scope, "coordinator_overlap"))
    overlap_fallback = None
    if overlaps is None:
        try:
            overlaps = request_overlap(
                eligible, budget=budget, model=model,
                trace_callback=trace_callback, trace_scope=trace_scope,
            )
        except Exception as exc:
            # The overlap pass is an optimization. The final selector still receives every
            # candidate and is independently required to remove duplicates. A provider timeout or
            # twice-malformed overlap response therefore must not fail the whole tailoring run.
            # Keeping every candidate is the conservative fallback: nothing is silently deleted.
            overlap_fallback = f"overlap prepass unavailable; selected from full pool: {exc}"
            logger.warning("%s", overlap_fallback)
            overlaps = {}
    survivors = [candidate for candidate in eligible if candidate["id"] not in overlaps]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload(job, survivors))},
    ]
    failure = None
    previous_result = None
    cached_selection = cache.get((trace_scope, "coordinator_selection"))
    if cached_selection is not None:
        return combine_selection(candidates, cached_selection, overlaps)

    input_payload = payload(job, survivors)
    for attempt_index in range(2):
        attempt = attempt_index + 1
        result = None
        event = _trace_event("coordinator_selection", trace_scope, attempt, model,
                             deepcopy(messages), input_payload)
        started = time.perf_counter()
        try:
            response = complete_json(
                messages,
                model=model or MODEL,
                budget=budget,
                kind="tailoring_review",
                timeout=60,
                schema=SELECTION_SCHEMA if trace_callback else None,
                schema_name="focused_coordinator_selection",
            )
            content = response.choices[0].message.content or "{}"
            event["raw_response"] = content
            event["usage"] = _usage(response)
            result = json.loads(content)
            try:
                chosen = validate(result, survivors, allow_missing=attempt_index > 0)
            except ContractViolation:
                if attempt_index == 0 or previous_result is None:
                    raise
                merged, repaired = _merge_corrective_decisions(previous_result, result)
                if not repaired:
                    raise
                chosen = validate(
                    merged,
                    survivors,
                    allow_missing=True,
                    repair_invalid_representatives=True,
                )
                chosen.setdefault("contract_repairs", []).append(
                    "merged the corrective decision patch with the prior response"
                )
            if overlap_fallback:
                chosen.setdefault("contract_repairs", []).append(overlap_fallback)
            event.update({"status": "completed", "normalized": chosen,
                          "validation": {"valid": True}})
        except json.JSONDecodeError as exc:
            logger.warning("question coordinator response was not valid JSON (%d chars): %s",
                           len(content), content[-120:])
            failure = ReviewUnavailable(
                f"the coordinator response was not valid JSON ({len(content)} chars)"
            )
            failure.__cause__ = exc
        except ContractViolation as exc:
            failure = exc
        except Exception as exc:
            failure = exc
        finally:
            event["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            if "status" not in event:
                event.update({"status": "failed", "error": str(failure),
                              "validation": {"valid": False,
                                             "error_type": type(failure).__name__}})
            if trace_callback:
                trace_callback(event)
        if event["status"] == "completed":
            return combine_selection(candidates, event["normalized"], overlaps)
        if isinstance(result, dict):
            previous_result = result
        if attempt_index == 0:
            messages.append({
                "role": "user",
                "content": (
                    "Your previous selection could not be used: " + str(failure) + ". "
                    "Return exactly one decision row per supplied id, with action select or "
                    "reject. Do not repeat an id. Preserve a distinct replacement after removing "
                    "project overlap."
                ),
            })
    raise failure


def coordinate(job, bullets, budget=None, model=None):
    """Collect candidates, make one selection call, and return immutable selected questions."""
    candidates = collect_candidates(bullets)
    if not candidates:
        return {"selected_ids": [], "selected": [], "rejected": []}
    return validate(
        request_selection(job, candidates, budget=budget, model=model),
        candidates,
    )
