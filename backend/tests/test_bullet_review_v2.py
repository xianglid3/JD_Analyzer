"""The server's reading of a question-generating review.

The model's judgment is measured by `evals/run_review_v2_eval.py` against the real thing. What is
pinned here is what the backend does with whatever comes back — and the two rules that exist
because a real run broke them:

* a question may reference only a requirement THIS bullet's evidence reaches. A React messaging
  bullet was asked "did you use data structures?": the phrase came from the posting, the match was
  inferred through React, and the resume-wide gap was attached to whichever bullet was nearest.
* a rewrite and a question cannot both be right. v1 dropped the question and kept the rewrite; the
  opposite was tried and asked a bullet that already named its endpoints which endpoints it meant.
  Neither reinterpretation is safe, so the response is retried and then abandoned.
"""

import pytest

from services.bullet_review import REVIEW_UNAVAILABLE
from services.bullet_review_v2 import (
    MAX_QUESTION_CANDIDATES,
    ContractViolation,
    fit_context,
    referenceable,
    validate,
)


BULLET = "Worked with PostgreSQL to store and manage application data."
TASK = {
    "bullet_id": "b",
    "text": BULLET,
    "entry": "JobMatcha — Developer",
    "siblings": ["Built an AI-powered platform that helps users improve their resumes."],
    "answers": [],
    "requirements": [{"requirement": "postgresql", "match": "supported_explicit",
                      "importance": "required"}],
    "supported_explicit": [{"id": "r2", "label": "postgresql", "importance": "required"}],
    "related_inferred": [],
    "claimed_not_demonstrated": [{"id": "r4", "label": "testing", "importance": "required"}],
    "resume_gaps": [{"id": "r5", "label": "kafka", "importance": "required"}],
}

CANDIDATE = {
    "id": "c1",
    "question": "Which part of storing and managing application data did you design or write yourself?",
    "missing_fact": "which part of the data layer they built",
    "recruiter_doubt_type": "contribution",
    "why_it_matters_for_this_job": "The role owns the data layer end to end.",
    "expected_resume_change": "The bullet could name the schema or the queries they wrote.",
    "priority": "high",
    "requirement_reference": "r2",
}

GOOD = {
    "bullet": "b1",
    "established_facts": ["They used PostgreSQL for application data"],
    "strength_assessment": "A recruiter can tell they touched a PostgreSQL data layer.",
    "rewrite_from_existing_evidence": None,
    "question_candidates": [CANDIDATE],
}


def test_a_justified_candidate_survives():
    result = validate(GOOD, TASK)
    assert len(result["question_candidates"]) == 1
    assert result["question_candidates"][0]["requirement_reference"] == "r2"
    assert result["strength_assessment"].startswith("A recruiter")
    assert result["dropped"] == []


def test_several_distinct_candidates_all_survive():
    """The whole point of the contract: one bullet, several genuinely different gaps."""
    second = {**CANDIDATE, "id": "c2", "recruiter_doubt_type": "scope",
              "question": "Which application data went through PostgreSQL — all of it, or one feature's?",
              "missing_fact": "how much of the data lived there",
              "expected_resume_change": "The bullet could say what the store actually held."}
    result = validate({**GOOD, "question_candidates": [CANDIDATE, second]}, TASK)
    assert [c["id"] for c in result["question_candidates"]] == ["c1", "c2"]


# ── the reference is scoped to this bullet ───────────────────────────────────

def test_only_this_bullets_own_requirements_are_referenceable():
    assert referenceable(TASK) == {"r2"}


@pytest.mark.parametrize("reference", ["r5", "r4", "r99"])
def test_a_reference_outside_the_bullets_context_is_refused(reference):
    """r5 is a resume-wide gap, r4 is claimed-but-not-demonstrated, r99 is invented. None of them
    is a reason to ask THIS bullet anything."""
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "requirement_reference": reference}]},
        TASK,
    )
    candidate = result["question_candidates"][0]
    assert candidate["requirement_reference"] is None
    assert candidate["reference_refused"] is True


def test_a_refused_reference_does_not_cost_the_question():
    """The question may be perfectly good and the label merely wrong. Dropping the candidate for
    it would let a bad reference destroy a real question."""
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "requirement_reference": "r5"}]},
        TASK,
    )
    assert len(result["question_candidates"]) == 1
    assert result["question_candidates"][0]["question"] == CANDIDATE["question"]


def test_no_reference_at_all_is_a_valid_answer():
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "requirement_reference": None}]}, TASK,
    )
    assert result["question_candidates"][0]["requirement_reference"] is None
    assert result["question_candidates"][0]["reference_refused"] is False


# ── a rewrite or questions, never both ───────────────────────────────────────

def test_a_rewrite_beside_questions_is_refused_not_repaired():
    with pytest.raises(ContractViolation):
        validate({**GOOD, "rewrite_from_existing_evidence": "Lead with the work and cut filler."},
                 TASK)


def test_a_rewrite_alone_is_fine():
    result = validate(
        {**GOOD, "question_candidates": [],
         "rewrite_from_existing_evidence": "Lead with the schema work and cut the filler."},
        TASK,
    )
    assert result["rewrite_from_existing_evidence"].startswith("Lead with")
    assert result["question_candidates"] == []


# ── a bad candidate is dropped, not fatal ────────────────────────────────────

@pytest.mark.parametrize("field", [
    "missing_fact", "why_it_matters_for_this_job", "expected_resume_change",
])
def test_a_candidate_that_cannot_show_its_work_is_dropped(field):
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, field: None}]}, TASK)
    assert result["question_candidates"] == []
    assert field in result["dropped"][0]["why"]


def test_a_question_that_quotes_nothing_from_the_bullet_is_dropped():
    astray = {**CANDIDATE, "question": "What tools did you use on this project, and why those?"}
    result = validate({**GOOD, "question_candidates": [astray]}, TASK)
    assert "quote" in result["dropped"][0]["why"]


def test_a_question_may_not_name_evidence_nobody_gave():
    invented = {**CANDIDATE,
                "question": "Which Redis cache sat in front of storing and managing application data?"}
    result = validate({**GOOD, "question_candidates": [invented]}, TASK)
    assert "redis" in result["dropped"][0]["why"]


def test_a_question_may_not_ask_the_postings_own_words_back():
    """The real failure this pins: the phrase comes from the job, not from the bullet."""
    task = {
        **TASK,
        "requirements": [{"requirement": "data structures or storage systems",
                          "match": "related_inferred", "importance": "required"}],
    }
    echoed = {**CANDIDATE,
              "question": "Which data structures did you use to store and manage application data?"}
    result = validate({**GOOD, "question_candidates": [echoed]}, task)
    assert "own words" in result["dropped"][0]["why"]
    assert "data structures" in result["dropped"][0]["why"]


def test_a_generic_question_is_dropped_even_when_anchored():
    generic = {**CANDIDATE,
               "question": "What was the impact of storing and managing application data?"}
    result = validate({**GOOD, "question_candidates": [generic]}, TASK)
    assert "impact" in result["dropped"][0]["why"]


def test_a_candidate_without_a_doubt_type_is_dropped():
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "recruiter_doubt_type": "vibes"}]}, TASK,
    )
    assert "kind of doubt" in result["dropped"][0]["why"]


def test_two_candidates_cannot_share_an_id():
    result = validate({**GOOD, "question_candidates": [CANDIDATE, {**CANDIDATE}]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert "id of its own" in result["dropped"][0]["why"]


def test_one_bad_candidate_does_not_cost_the_good_ones():
    good_two = {**CANDIDATE, "id": "c2", "recruiter_doubt_type": "scope",
                "question": "Which application data went through PostgreSQL, and which did not?",
                "missing_fact": "how much of the data lived there",
                "expected_resume_change": "The bullet could say what the store held."}
    bad = {**CANDIDATE, "id": "c3", "question": "What was the impact of that work?"}
    result = validate({**GOOD, "question_candidates": [CANDIDATE, good_two, bad]}, TASK)
    assert [c["id"] for c in result["question_candidates"]] == ["c1", "c2"]
    assert result["dropped"][0]["id"] == "c3"


def test_an_unreadable_priority_is_unranked_not_middling():
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, "priority": "urgent"}]}, TASK)
    assert result["question_candidates"][0]["priority"] is None


def test_more_candidates_than_the_validator_accepts_are_cut_and_reported():
    many = [
        {**CANDIDATE, "id": f"c{i}"} if i == 1 else {
            **CANDIDATE, "id": f"c{i}",
            "question": f"Which of the {i} parts of storing and managing application data was yours?",
        }
        for i in range(1, MAX_QUESTION_CANDIDATES + 4)
    ]
    result = validate({**GOOD, "question_candidates": many}, TASK)
    assert len(result["question_candidates"]) == MAX_QUESTION_CANDIDATES
    assert any(str(MAX_QUESTION_CANDIDATES) in drop["why"] for drop in result["dropped"])


def test_a_bullet_the_review_left_out_is_not_a_review():
    for nothing in (None, {}, "reviewed"):
        result = validate(nothing, TASK)
        assert result["decision"] == REVIEW_UNAVAILABLE
        assert result["question_candidates"] == []
        assert result["strength_assessment"] is None
        assert result["unavailable_reason"]


# ── the fit context is derived from the assessment, never from labels ─────────

def test_fit_context_separates_the_four_kinds():
    """Ids come from the assessment's own ordering, so they mean the same thing to the reviewer,
    the plan, and anything reading a stored review."""
    assessment = {"requirements": [
        {"requirement": "python", "state": "EXPLICIT", "importance": "required",
         "evidence": [{"bullet_id": "bullet-1"}]},
        {"requirement": "front-end frameworks", "state": "INFERRED", "importance": "preferred",
         "inferred_from": ["react"], "evidence": [{"bullet_id": "bullet-1"}]},
        {"requirement": "kafka", "state": "NONE", "importance": "required", "evidence": []},
        {"requirement": "testing", "state": "EXPLICIT", "importance": "required", "evidence": []},
        {"requirement": "go", "state": "EXPLICIT", "importance": "required",
         "evidence": [{"bullet_id": "somebody-else"}]},
    ]}
    from services.tailoring_plan import build_tailoring_plan

    plan = build_tailoring_plan(assessment)
    context = fit_context(assessment, plan, "bullet-1")

    assert [item["id"] for item in context["supported_explicit"]] == ["r0"]
    assert [item["id"] for item in context["related_inferred"]] == ["r1"]
    assert context["related_inferred"][0]["inferred_from"] == ["react"]
    # a requirement another bullet's evidence cites is not this bullet's context
    assert "r4" not in {item["id"] for item in context["supported_explicit"]}
    # and the resume-wide lists are populated but unreferenceable
    gap_ids = {item["id"] for item in context["resume_gaps"]}
    claimed_ids = {item["id"] for item in context["claimed_not_demonstrated"]}
    assert "r2" in gap_ids
    assert not referenceable({**TASK, **context}) & (gap_ids | claimed_ids)
