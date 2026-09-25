"""Experimental evidence-first project question planning. Never called by production.

The first measured project prompt still saw the job while deciding whether a bullet was weak. It
asked about every bullet and turned Google requirements into algorithms/data-structures questions.
This version makes that failure structurally impossible: the evidence audit receives no job, and
the relevance pass can only rank weakness ids emitted by that audit.
"""

import json

from services.bullet_review import ReviewUnavailable, squash
from services.bullet_review_v2 import DOUBT_TYPES, MODEL


ASSESSMENTS = {"SUFFICIENT", "REWRITE", "UNCERTAIN"}
MAX_WEAKNESSES_PER_BULLET = 20


EVIDENCE_PROMPT = """Review the resume bullets in one project or role. Return JSON.
Judge whether a reader can identify the work done at resume depth, not reproduce its implementation.
You receive no job description. Read the entry and supplied answers together.

Judge what the complete evidence establishes, without assigning ownership from a verb list.
Does the description identify concrete work or merely state an activity and benefit? Concrete
functionality, a specific behavior/boundary, a named method applied to a task, or a described
verification can be enough. A resume bullet is not a design document and does not need all of these.

Distinguish a technique's definition from an implementation choice: "Added rate limiting to limit
requests" merely restates what rate limiting means. "Enforced a per-account request budget" names
the boundary that was implemented and is sufficient without the limiting algorithm or metrics.
Apply this distinction to claimed reliability or duplicate-prevention work as well; the intended
benefit alone does not identify the concrete change.

SUFFICIENT: The target plus entry context already describes concrete work. Stop there. Do not ask
how each named technique was implemented, how components were integrated, which individual
test cases ran, or what impact each feature had just because more detail is possible. Do not
demand metrics, algorithms, technologies, challenges, or leadership absent from the claim.
Name the existing facts that identify the work in the reason and established_facts.

UNCERTAIN: Identify a specific missing fact about a claim that remains materially vague after
reading siblings and answers. A claimed improvement with no stated change is an implementation
gap even when ownership is clear. A category such as "validation" without the rule/check may be a
gap if it carries the contribution. An overview needs no duplicated implementation when siblings
already explain that contribution. Do not equate a named task or feature with a vague category.

Contrast examples (principles, not phrases to match):
- "Improved export reliability so failed exports recover": UNCERTAIN, the change that enables
  recovery is absent. "Resumed exports from saved batch offsets after failure": SUFFICIENT;
  the storage format, code path and additional tests are optional elaboration.
- "Added caching to make reports faster": UNCERTAIN, what was cached is absent.
  "Cached daily inventory totals": SUFFICIENT, concrete work is now identifiable. Cache product,
  eviction algorithm, benchmarks and refresh implementation are not automatically owed.
- "Built a booking API with backend validation": if entry context does not explain the claimed
  validation, its checks may be UNCERTAIN. "Built booking endpoints that create/cancel appointments
  and reject overlapping reservations": SUFFICIENT; do not request endpoint internals.
- "Set up tests and monitoring": coverage or monitored behavior may be UNCERTAIN.
  "Tested controller shutdown on voltage faults with unit tests": SUFFICIENT without individual
  cases. "Deployed containerized services with named CI/monitoring tools and described test
  coverage": SUFFICIENT without configuration files or service-interaction explanations.
- "Automated invoice calculations with spreadsheet formulas": SUFFICIENT. The formulas and
  routine steps are optional depth, not evidence of an unclear contribution.

For UNCERTAIN quote the exact target phrase and specify the one new factual clause an answer
would add, without inventing its value. "More implementation details", "how all features work",
and "impact on the project" do not identify a missing clause. Explain why entry context does not
settle it. For ownership gaps, seek the owned work rather than asking again for techniques already
listed. When an overview and a subsystem bullet share an ownership gap, favor locating the precise
gap in the subsystem instead of creating a second broad version of it.

Multiple weaknesses are welcome when they seek independently useful facts; there is no minimum.
"What changed to improve recovery" and "how was recovery achieved" are one missing fact.
Separate clauses such as an unspecified validation rule and an unspecified matching input may
justify different questions. Do not split one gap into broad and narrow paraphrases.

REWRITE: Existing facts are sufficient but a specific wording problem hides them. Name that
wording problem and an instruction using only existing facts. "Add specifics" is not a rewrite
if the needed fact is absent. Do not rewrite merely because different wording is possible.

Return exactly one row per supplied bullet_id:
{"bullets": [{
  "bullet_id": "id",
  "assessment": "SUFFICIENT|REWRITE|UNCERTAIN",
  "reason": "evidence-only reason",
  "established_facts": ["facts supported by target, siblings, or supplied answers"],
  "rewrite_instruction": null,
  "weaknesses": [{
    "id": "w1",
    "source_quote": "exact phrase from target bullet",
    "missing_clause": "precise factual clause a truthful answer could add",
    "why_unresolved": "why target, siblings and answers do not settle it",
    "recruiter_doubt_type": "contribution|implementation|scope|result_validation|clarification"
  }]
}]}

SUFFICIENT has no rewrite or weaknesses. REWRITE has one rewrite and no weaknesses. UNCERTAIN has
one or more weaknesses and no rewrite. A ceiling is not a quota.
"""


RELEVANCE_PROMPT = """Rank already-audited resume weaknesses for one specific job and write the
questions worth asking. Return JSON. You may not create, broaden, split, or rename a weakness.

Every supplied weakness was found without seeing this job. Decide ASK or NOT_MATERIAL for that
exact weakness. The job may increase or decrease its priority; a job requirement cannot become a
new weakness. Do not ask for algorithms, data structures, technologies, scale, metrics, challenges,
leadership, or outcomes unless the supplied missing_clause explicitly seeks that fact.

ASK only when a truthful answer could add the supplied missing_clause to the target bullet and
that clause would materially improve this resume for this job. Write one focused question for the
weakness. It must permit answers such as "none" or "that was not my part" and must not assume the
missing fact exists. NOT_MATERIAL is correct when the weakness is irrelevant or too minor to spend
the candidate's time.

Use siblings and established facts to reject already-settled or overlapping weaknesses. Keep
different weaknesses only when their answers would add different resume clauses. Generic role,
technology, challenge, and interview-story questions are low value. requirement_reference may be
null or one of the supplied referenceable_ids; it ranks relevance and never proves a candidate
fact.

Return exactly one row for every supplied weakness:
{"weaknesses": [{
  "bullet_id": "id",
  "weakness_id": "w1",
  "disposition": "ASK|NOT_MATERIAL",
  "reason": "why this exact missing clause is or is not worth asking for this job",
  "question": "one focused question, or null",
  "why_it_matters_for_this_job": "specific relevance, or null",
  "priority": "high|medium|low|null",
  "requirement_reference": "supplied id or null"
}]}

ASK requires question, relevance, and priority. NOT_MATERIAL requires all three to be null.
"""


def evidence_payload(tasks):
    """Resume evidence only. No job or fit data may cross this boundary."""
    return {
        "entry": tasks[0].get("entry", "") if tasks else "",
        "bullets": [
            {
                "bullet_id": task["bullet_id"],
                "target_bullet": task["text"],
                "sibling_bullets": task.get("siblings") or [],
                "answers_given_in_this_run": task.get("answers") or [],
            }
            for task in tasks
        ],
    }


def _fit_ids(task):
    return {
        item.get("id")
        for key in ("supported_explicit", "related_inferred")
        for item in task.get(key) or []
        if item.get("id")
    }


def validate_evidence(data, tasks):
    if not isinstance(data, dict) or not isinstance(data.get("bullets"), list):
        raise ReviewUnavailable("evidence audit must return a bullets array")
    by_id = {task["bullet_id"]: task for task in tasks}
    audits = {}
    for row in data["bullets"]:
        if not isinstance(row, dict):
            raise ReviewUnavailable("evidence audit rows must be objects")
        bullet_id = row.get("bullet_id")
        if bullet_id not in by_id or bullet_id in audits:
            raise ReviewUnavailable("evidence audit returned an unknown or repeated bullet")
        assessment = row.get("assessment")
        reason = row.get("reason")
        facts = row.get("established_facts")
        rewrite = row.get("rewrite_instruction")
        weaknesses = row.get("weaknesses")
        if (assessment not in ASSESSMENTS or not isinstance(reason, str) or not reason.strip()
                or not isinstance(facts, list)
                or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
                or not isinstance(weaknesses, list)
                or len(weaknesses) > MAX_WEAKNESSES_PER_BULLET):
            raise ReviewUnavailable("invalid evidence assessment")
        if (assessment == "SUFFICIENT" and (rewrite is not None or weaknesses)
                or assessment == "REWRITE" and (
                    not isinstance(rewrite, str) or not rewrite.strip() or weaknesses)
                or assessment == "UNCERTAIN" and (rewrite is not None or not weaknesses)):
            raise ReviewUnavailable("contradictory evidence assessment")

        seen, normalized = set(), []
        for weakness in weaknesses:
            required = ("id", "source_quote", "missing_clause", "why_unresolved",
                        "recruiter_doubt_type")
            if (not isinstance(weakness, dict)
                    or any(not isinstance(weakness.get(key), str)
                           or not weakness[key].strip() for key in required)):
                raise ReviewUnavailable("weakness lacks a quote or precise missing clause")
            weakness_id = weakness["id"]
            if weakness_id in seen:
                raise ReviewUnavailable("weakness ids must be unique within a bullet")
            if weakness["recruiter_doubt_type"] not in DOUBT_TYPES:
                raise ReviewUnavailable("weakness has an invalid recruiter-doubt type")
            if squash(weakness["source_quote"]) not in squash(by_id[bullet_id]["text"]):
                raise ReviewUnavailable("weakness source_quote must occur in the target bullet")
            seen.add(weakness_id)
            normalized.append(dict(weakness))
        audits[bullet_id] = {
            "assessment": assessment,
            "reason": reason.strip(),
            "established_facts": [fact.strip() for fact in facts],
            "rewrite_instruction": rewrite.strip() if isinstance(rewrite, str) else None,
            "weaknesses": normalized,
        }
    if set(audits) != set(by_id):
        raise ReviewUnavailable("evidence audit omitted a bullet")
    return audits


def relevance_payload(job, tasks, audits):
    title, company, summary, requirements = job
    by_id = {task["bullet_id"]: task for task in tasks}
    targets = []
    for bullet_id, audit in audits.items():
        if audit["assessment"] != "UNCERTAIN":
            continue
        task = by_id[bullet_id]
        targets.append({
            "bullet_id": bullet_id,
            "target_bullet": task["text"],
            "sibling_bullets": task.get("siblings") or [],
            "answers_given_in_this_run": task.get("answers") or [],
            "established_facts": audit["established_facts"],
            "weaknesses": audit["weaknesses"],
            "referenceable_ids": sorted(_fit_ids(task)),
            "fit_context": {
                key: task.get(key) or []
                for key in ("supported_explicit", "related_inferred")
            },
        })
    return {
        "job_description": {
            "title": title,
            "company": company,
            "summary": summary,
            "requirements": list(requirements or []),
        },
        "audited_targets": targets,
    }


def validate_relevance(data, tasks, audits):
    if not isinstance(data, dict) or not isinstance(data.get("weaknesses"), list):
        raise ReviewUnavailable("relevance pass must return a weaknesses array")
    by_id = {task["bullet_id"]: task for task in tasks}
    expected = {
        (bullet_id, weakness["id"]): weakness
        for bullet_id, audit in audits.items()
        if audit["assessment"] == "UNCERTAIN"
        for weakness in audit["weaknesses"]
    }
    decisions = {}
    for row in data["weaknesses"]:
        if not isinstance(row, dict):
            raise ReviewUnavailable("relevance rows must be objects")
        key = (row.get("bullet_id"), row.get("weakness_id"))
        if key not in expected or key in decisions:
            raise ReviewUnavailable("relevance pass returned an unknown or repeated weakness")
        disposition = row.get("disposition")
        reason = row.get("reason")
        question = row.get("question")
        why = row.get("why_it_matters_for_this_job")
        priority = row.get("priority")
        reference = row.get("requirement_reference")
        if (disposition not in {"ASK", "NOT_MATERIAL"}
                or not isinstance(reason, str) or not reason.strip()):
            raise ReviewUnavailable("invalid weakness disposition")
        if disposition == "ASK":
            if (not isinstance(question, str) or question.count("?") != 1 or "\n" in question
                    or not isinstance(why, str) or not why.strip()
                    or priority not in {"high", "medium", "low"}):
                raise ReviewUnavailable("ASK weakness needs one question, relevance and priority")
        elif question is not None or why is not None or priority is not None:
            raise ReviewUnavailable("NOT_MATERIAL weakness cannot carry question fields")
        if reference is not None and reference not in _fit_ids(by_id[key[0]]):
            raise ReviewUnavailable("unknown or non-referenceable job requirement")
        decisions[key] = {
            "disposition": disposition,
            "reason": reason.strip(),
            "question": question.strip() if isinstance(question, str) else None,
            "why_it_matters_for_this_job": why.strip() if isinstance(why, str) else None,
            "priority": priority,
            "requirement_reference": reference,
        }
    if set(decisions) != set(expected):
        raise ReviewUnavailable("relevance pass omitted an audited weakness")
    return decisions


def assemble_reviews(tasks, audits, relevance):
    """Adapt the two enforced phases to the existing coordinator candidate boundary."""
    reviews = {}
    for task in tasks:
        bullet_id = task["bullet_id"]
        audit = audits[bullet_id]
        candidates, uncertainties = [], []
        for weakness in audit["weaknesses"]:
            uncertainties.append({
                **weakness,
                "evidence_quote": weakness["source_quote"],
                "missing_fact": weakness["missing_clause"],
                "expected_resume_change": weakness["missing_clause"],
            })
            decision = relevance.get((bullet_id, weakness["id"]))
            if not decision or decision["disposition"] != "ASK":
                continue
            candidates.append({
                "id": f"c{len(candidates) + 1}",
                "uncertainty_id": weakness["id"],
                "source_quote": weakness["source_quote"],
                "question": decision["question"],
                "missing_fact": weakness["missing_clause"],
                "recruiter_doubt_type": weakness["recruiter_doubt_type"],
                "why_it_matters_for_this_job": decision["why_it_matters_for_this_job"],
                "expected_resume_change": weakness["missing_clause"],
                "priority": decision["priority"],
                "requirement_reference": decision["requirement_reference"],
            })

        if audit["assessment"] == "REWRITE":
            final_decision = "REWRITE"
        elif candidates:
            final_decision = "ASK"
        else:
            final_decision = "KEEP"
        reviews[bullet_id] = {
            "decision_claimed": final_decision,
            "decision_reason": audit["reason"],
            "established_facts": audit["established_facts"],
            "strength_assessment": audit["reason"],
            "evidence_assessment": audit["assessment"],
            "rewrite_from_existing_evidence": (
                audit["rewrite_instruction"] if final_decision == "REWRITE" else None
            ),
            "uncertainties": uncertainties,
            "question_candidates": candidates,
        }
    return reviews


def _json_response(response, stage):
    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReviewUnavailable(f"{stage} returned malformed JSON") from exc


def plan_project(job, tasks, model=MODEL):
    """Audit without the job, then rank only confirmed weaknesses with the job."""
    from services.openai_services import complete_json

    evidence_response = complete_json(
        [
            {"role": "system", "content": EVIDENCE_PROMPT},
            {"role": "user", "content": json.dumps(evidence_payload(tasks))},
        ],
        model=model,
        kind="tailoring_review",
        timeout=60,
    )
    audits = validate_evidence(_json_response(evidence_response, "evidence audit"), tasks)
    if not any(audit["assessment"] == "UNCERTAIN" for audit in audits.values()):
        return assemble_reviews(tasks, audits, {})

    relevance_response = complete_json(
        [
            {"role": "system", "content": RELEVANCE_PROMPT},
            {"role": "user", "content": json.dumps(relevance_payload(job, tasks, audits))},
        ],
        model=model,
        kind="tailoring_review",
        timeout=60,
    )
    relevance = validate_relevance(
        _json_response(relevance_response, "relevance pass"), tasks, audits
    )
    return assemble_reviews(tasks, audits, relevance)
