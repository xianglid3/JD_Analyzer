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

import json
from types import SimpleNamespace

import pytest

from services import bullet_review_v2 as reviewer
from services.bullet_review import REVIEW_UNAVAILABLE
from services.bullet_review_v2 import (
    MAX_QUESTION_CANDIDATES,
    ContractViolation,
    already_answered,
    evaluation_problems,
    fit_context,
    prompt_for,
    referenceable,
    related_to_bullet,
    request_payload,
    review,
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
    "related_partial": [],
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

GAP_SCAN = {
    "contribution": "ask",
    "implementation": "settled",
    "scope": "settled",
    "result_validation": "not_material",
    "clarification": "settled",
}

GAP = {"id": "g1", "recruiter_doubt_type": "contribution",
       "missing_fact": "the storage work personally implemented",
       "evidence_quote": "Worked with PostgreSQL", "why_unanswered": "Worked with does not identify the change.",
       "expected_resume_change": "Name the storage operation implemented."}

GOOD = {
    "bullet": "b1",
    "decision": "ASK",
    "decision_reason": "The candidate's personal database contribution is still unclear.",
    "established_facts": ["They used PostgreSQL for application data"],
    "strength_assessment": "A recruiter can tell they touched a PostgreSQL data layer.",
    "rewrite_from_existing_evidence": None,
    "gap_scan": GAP_SCAN,
    "question_candidates": [CANDIDATE],
}


def test_prompt_has_no_ownership_word_definitions():
    prompt = prompt_for()
    assert "Do not choose KEEP merely because the technologies align" in prompt
    assert "without assigning ownership from a verb list" in prompt
    assert "Strong ownership verbs count" not in prompt
    assert "Read the grammatical subject as the candidate" not in prompt


def test_prompt_requests_distinct_alternatives_without_turning_the_ceiling_into_a_target():
    prompt = prompt_for()
    assert "do not stop after finding the first question" in prompt
    assert "Two to four candidates are reasonable" in prompt
    assert "Do not create paraphrases" in prompt
    assert "A strong or irrelevant bullet should still produce zero" in prompt
    assert "complete `gap_scan` for all five recruiter-doubt lenses" in prompt
    assert all(name in prompt for name in GAP_SCAN)
    assert "database?\" is not a substitute" in prompt


def test_alternative_prompt_requests_json_and_excludes_the_primary_question():
    prompt = reviewer.ALTERNATIVE_PROMPT
    assert "JSON object" in prompt
    assert "missing facts are exclusions" in prompt
    assert "Return an empty list" in prompt


def test_triage_prompt_separates_the_strength_decision_from_question_generation():
    prompt = prompt_for(triage_only=True)
    assert "TRIAGE ONLY" in prompt
    assert "does not write question text" in prompt
    assert '"question_candidates": []' in prompt
    assert "Two to four candidates are reasonable" not in prompt
    assert "A claimed improvement is not automatically a concrete contribution" in prompt
    assert "Improved the reliability of" in prompt
    assert "Implemented reserve-before-spend idempotency" in prompt

    triage = {**GOOD, "question_candidates": [], "uncertainties": [GAP]}
    result = validate(triage, TASK, triage_only=True)
    assert result["decision_claimed"] == "ASK"
    assert result["question_candidates"] == []

    with pytest.raises(ContractViolation, match="triage must not generate"):
        validate(GOOD, TASK, triage_only=True)


def test_single_target_contract_has_no_list_or_model_owned_target_key():
    prompt = prompt_for(triage_only=True, single_target=True)
    request = request_payload(("Backend", "Acme", "", ["postgresql"]), [TASK])

    assert "exactly one top-level `target` object" in prompt
    assert "without a reviews array or bullet key" in prompt
    assert '"reviews"' not in prompt.split("OUTPUT", 1)[-1]
    assert set(request) == {"job_description", "target"}
    assert "bullet" not in request["target"]
    assert request["target"]["target_bullet"] == BULLET


def test_prompt_pins_the_human_labelled_failure_classes():
    prompt = prompt_for(triage_only=True, single_target=True)
    alternatives = reviewer.ALTERNATIVE_PROMPT

    assert "without assigning ownership from a verb list" in prompt
    assert "Never convert a job requirement into a bullet weakness" in prompt
    assert "not merely useful interview preparation" in reviewer.prompt_for(single_target=True)
    assert "would only make an interview story" in alternatives
    assert "for algorithms, data structures, distributed systems, scalability" in alternatives


def test_a_justified_candidate_survives():
    result = validate(GOOD, TASK)
    assert len(result["question_candidates"]) == 1
    assert result["question_candidates"][0]["requirement_reference"] == "r2"
    assert result["strength_assessment"].startswith("A recruiter")
    assert result["hard_rejected"] == []


def test_several_distinct_candidates_all_survive():
    """The whole point of the contract: one bullet, several genuinely different gaps."""
    second = {**CANDIDATE, "id": "c2", "recruiter_doubt_type": "scope",
              "question": "Which application data went through PostgreSQL — all of it, or one feature's?",
              "missing_fact": "how much of the data lived there",
              "expected_resume_change": "The bullet could say what the store actually held."}
    result = validate({
        **GOOD,
        "gap_scan": {**GAP_SCAN, "scope": "ask"},
        "question_candidates": [CANDIDATE, second],
    }, TASK)
    assert [c["id"] for c in result["question_candidates"]] == ["c1", "c2"]


def test_second_pass_adds_a_distinct_candidate_with_a_server_assigned_id(monkeypatch):
    alternative = {
        "question": "Which application data did PostgreSQL store for this work?",
        "missing_fact": "the data stored in PostgreSQL",
        "recruiter_doubt_type": "scope",
        "why_it_matters_for_this_job": "The role owns backend data boundaries.",
        "expected_resume_change": "The bullet could name the records the database stored.",
        "priority": "medium",
        "requirement_reference": "r2",
    }
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"question_candidates": [alternative]})
    ))])
    monkeypatch.setattr("services.openai_services.complete_json", lambda *a, **k: response)

    result = reviewer.expand_review(
        ("Backend Engineer", "Acme", "", ["postgresql"]), TASK,
        validate({**GOOD, "gap_scan": {**GAP_SCAN, "scope": "ask"}}, TASK),
    )

    assert [item["id"] for item in result["question_candidates"]] == ["c1", "c2"]
    assert result["question_candidates"][1]["question"] == alternative["question"]
    assert result["gap_scan"]["scope"] == "ask"


def test_second_pass_cannot_add_an_exact_duplicate(monkeypatch):
    duplicate = {
        key: value for key, value in CANDIDATE.items() if key != "id"
    }
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"question_candidates": [duplicate]})
    ))])
    monkeypatch.setattr("services.openai_services.complete_json", lambda *a, **k: response)

    result = reviewer.expand_review(
        ("Backend Engineer", "Acme", "", ["postgresql"]), TASK, validate(GOOD, TASK),
    )

    assert [item["id"] for item in result["question_candidates"]] == ["c1"]
    assert result["hard_rejected"][-1]["why"] == "duplicates an existing candidate"


def test_second_pass_never_changes_a_keep_decision(monkeypatch):
    kept = validate({
        **GOOD,
        "decision": "KEEP",
        "decision_reason": "The bullet is already clear.",
        "gap_scan": {key: "settled" for key in GAP_SCAN},
        "question_candidates": [],
    }, TASK)
    monkeypatch.setattr(
        reviewer, "request_alternatives", lambda *a, **k: pytest.fail("must not call"),
    )
    assert reviewer.expand_review(("Role", "Co", "", []), TASK, kept) == kept


# ── the reference is scoped to this bullet ───────────────────────────────────

def test_only_this_bullets_own_requirements_are_referenceable():
    assert referenceable(TASK) == {"r2"}


@pytest.mark.parametrize("reference", ["r5", "r4", "r99"])
def test_a_reference_outside_the_bullets_context_is_removed(reference):
    """Wrong ranking metadata cannot delete an otherwise grounded question."""
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "requirement_reference": reference}]},
        TASK,
    )
    candidate = result["question_candidates"][0]
    assert candidate["requirement_reference"] is None
    assert candidate["removed_requirement_reference"] == reference
    assert result["hard_rejected"] == []


def test_an_invalid_reference_cannot_smuggle_its_requirement_into_the_question():
    result = validate({
        **GOOD,
        "question_candidates": [{
            **CANDIDATE,
            "question": "How did Kafka change storing and managing application data?",
            "requirement_reference": "r5",
        }],
    }, TASK)
    assert result["question_candidates"] == []
    assert "unsupported referenced requirement" in result["hard_rejected"][0]["why"]


def test_no_reference_at_all_is_a_valid_answer():
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "requirement_reference": None}]}, TASK,
    )
    assert result["question_candidates"][0]["requirement_reference"] is None
    assert result["hard_rejected"] == []


# ── a rewrite or questions, never both ───────────────────────────────────────

def test_a_rewrite_beside_questions_is_refused_not_repaired():
    with pytest.raises(ContractViolation):
        validate({**GOOD, "rewrite_from_existing_evidence": "Lead with the work and cut filler."},
                 TASK)


def test_a_rewrite_alone_is_fine():
    result = validate(
        {**GOOD, "decision": "REWRITE", "question_candidates": [],
         "gap_scan": {key: "settled" for key in GAP_SCAN},
         "rewrite_from_existing_evidence": "Lead with the schema work and cut the filler."},
        TASK,
    )
    assert result["rewrite_from_existing_evidence"].startswith("Lead with")
    assert result["question_candidates"] == []


# ── a bad candidate is dropped, not fatal ────────────────────────────────────

@pytest.mark.parametrize("field", ["why_it_matters_for_this_job", "expected_resume_change"])
def test_a_terse_field_is_a_warning_not_a_rejection(field):
    """Every one of the thirteen candidates the first measurement deleted went for a two-word
    noun phrase, and not one of them went for being wrong. Terseness is now reported and kept."""
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, field: "too short"}]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert any(field in warning for warning in result["question_candidates"][0]["warnings"])
    assert result["hard_rejected"] == []


def test_a_two_word_missing_fact_is_kept_with_a_warning():
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "missing_fact": "checkout changes"}]},
        TASK,
    )
    assert len(result["question_candidates"]) == 1
    assert "missing_fact is terse" in result["question_candidates"][0]["warnings"]


def test_a_missing_field_is_still_a_rejection():
    """Terse is a warning; absent is a schema error."""
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, "missing_fact": None}]}, TASK)
    assert result["question_candidates"] == []
    assert "schema" in result["hard_rejected"][0]["why"]


def test_a_question_sharing_nothing_with_the_bullet_is_warned_not_rejected():
    """Lexical overlap cannot prove semantic relevance, so it is diagnostic rather than a gate."""
    astray = {**CANDIDATE, "question": "What tools were chosen here, and by whom?"}
    result = validate({**GOOD, "question_candidates": [astray]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert any("shares no meaningful words" in warning
               for warning in result["question_candidates"][0]["warnings"])
    assert result["hard_rejected"] == []


def test_failing_the_exact_anchor_is_only_a_warning():
    # shares "data" with the bullet, so it is related — but quotes no consecutive pair and names
    # no skill, which is what `quotes_bullet` looks for
    loose = {**CANDIDATE,
             "question": "Which parts of that data work were yours rather than a teammate's?"}
    result = validate({**GOOD, "question_candidates": [loose]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert any("consecutive" in w for w in result["question_candidates"][0]["warnings"])


def test_relatedness_excludes_siblings_but_counts_answers_for_this_bullet():
    assert related_to_bullet("Which PostgreSQL table held the jobs?", TASK)
    sibling_only = {**TASK, "siblings": ["Built the payments platform architecture."]}
    assert not related_to_bullet("Which platform architecture did you lead?", sibling_only)
    answered = {**TASK, "answers": ["I wrote the nightly reconciliation job."]}
    assert related_to_bullet("Which reconciliation job was yours?", answered)
    assert not related_to_bullet("What tools were chosen, and by whom?", TASK)


def test_a_question_may_not_name_evidence_nobody_gave():
    invented = {**CANDIDATE,
                "question": "Which Redis cache sat in front of storing and managing application data?"}
    result = validate({**GOOD, "question_candidates": [invented]}, TASK)
    assert result["question_candidates"] == []
    assert "redis" in result["hard_rejected"][0]["why"]


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
    assert result["question_candidates"] == []
    assert "data structures" in result["hard_rejected"][0]["why"]


def test_generic_phrasing_is_a_warning_not_a_rejection():
    """It is a phrasing heuristic, and the template that survived every guard in the first
    measurement — "What specific X did you Y?" — was never caught by it. Reported, not deleted."""
    generic = {**CANDIDATE,
               "question": "What was the impact of storing and managing application data?"}
    result = validate({**GOOD, "question_candidates": [generic]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert any("impact" in w for w in result["question_candidates"][0]["warnings"])


def test_a_candidate_without_a_valid_doubt_type_is_rejected():
    result = validate(
        {**GOOD, "question_candidates": [{**CANDIDATE, "recruiter_doubt_type": "vibes"}]}, TASK,
    )
    assert result["question_candidates"] == []
    assert "schema" in result["hard_rejected"][0]["why"]


def test_two_candidates_cannot_share_an_id():
    result = validate({**GOOD, "question_candidates": [CANDIDATE, {**CANDIDATE}]}, TASK)
    assert len(result["question_candidates"]) == 1
    assert "duplicate" in result["hard_rejected"][0]["why"]


def test_one_bad_candidate_does_not_cost_the_good_ones():
    good_two = {**CANDIDATE, "id": "c2", "recruiter_doubt_type": "scope",
                "question": "Which application data went through PostgreSQL, and which did not?",
                "missing_fact": "how much of the data lived there",
                "expected_resume_change": "The bullet could say what the store held."}
    bad = {**CANDIDATE, "id": "c3",
           "question": "Which Redis cache sat in front of all of that?"}
    result = validate({
        **GOOD,
        "gap_scan": {**GAP_SCAN, "scope": "ask"},
        "question_candidates": [CANDIDATE, good_two, bad],
    }, TASK)
    assert [c["id"] for c in result["question_candidates"]] == ["c1", "c2"]
    assert result["hard_rejected"][0]["id"] == "c3"


def test_an_unreadable_priority_is_a_schema_rejection():
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, "priority": "urgent"}]}, TASK)
    assert result["question_candidates"] == []
    assert "priority" in result["hard_rejected"][0]["why"]


def test_lexical_answer_coverage_is_a_warning_not_a_rejection():
    """Word containment cannot prove that an answer semantically settled the question."""
    answered = {
        **TASK,
        "answers": ["I designed the PostgreSQL schema for jobs, resumes and runs."],
    }
    asking_again = {**CANDIDATE, "missing_fact": "the PostgreSQL schema",
                    "question": "Which PostgreSQL schema did you design for application data?"}
    result = validate({**GOOD, "question_candidates": [asking_again]}, answered)
    assert len(result["question_candidates"]) == 1
    assert any("may already appear" in warning
               for warning in result["question_candidates"][0]["warnings"])
    assert result["hard_rejected"] == []


def test_a_genuine_follow_up_is_not_mistaken_for_an_answered_one():
    """`already_answered` is conservative on purpose: a follow-up that introduces a new word is
    not covered by the answer, and a looser rule here would delete real questions."""
    answered = {
        **TASK,
        "answers": ["I designed the PostgreSQL schema for jobs, resumes and runs."],
    }
    assert not already_answered({"missing_fact": "which queries they wrote"}, answered)
    assert already_answered({"missing_fact": "the PostgreSQL schema"}, answered)


def test_same_words_do_not_hard_reject_a_genuine_follow_up():
    answered = {
        **TASK,
        "answers": ["I worked on schema design, but the migration belonged to someone else."],
    }
    follow_up = {
        **CANDIDATE,
        "question": "Which schema design decisions for application data were yours?",
        "missing_fact": "schema design",
    }
    result = validate({**GOOD, "question_candidates": [follow_up]}, answered)
    assert len(result["question_candidates"]) == 1
    assert result["hard_rejected"] == []


@pytest.mark.parametrize("field", ["why_it_matters_for_this_job", "expected_resume_change"])
def test_a_missing_selection_field_is_a_schema_rejection(field):
    result = validate({**GOOD, "question_candidates": [{**CANDIDATE, field: None}]}, TASK)
    assert result["question_candidates"] == []
    assert field in result["hard_rejected"][0]["why"]


@pytest.mark.parametrize(
    ("decision", "rewrite", "questions"),
    [
        ("KEEP", None, [CANDIDATE]),
        ("KEEP", "Rewrite it.", []),
        ("REWRITE", None, []),
        ("ASK", None, []),
    ],
)
def test_decision_contract_rejects_inconsistent_output(decision, rewrite, questions):
    with pytest.raises(ContractViolation):
        validate(
            {
                **GOOD,
                "decision": decision,
                "rewrite_from_existing_evidence": rewrite,
                "question_candidates": questions,
            },
            TASK,
        )


def test_decision_contract_requires_a_decision_and_reason():
    with pytest.raises(ContractViolation):
        validate({**GOOD, "decision": None}, TASK)
    with pytest.raises(ContractViolation):
        validate({**GOOD, "decision_reason": None}, TASK)


def test_decision_contract_requires_a_complete_gap_scan():
    with pytest.raises(ContractViolation, match="scan all five"):
        validate({**GOOD, "gap_scan": None}, TASK)
    with pytest.raises(ContractViolation, match="gap_scan values"):
        validate({**GOOD, "gap_scan": {**GAP_SCAN, "scope": "maybe"}}, TASK)


def test_candidates_must_come_from_lenses_marked_ask():
    scoped = {**CANDIDATE, "recruiter_doubt_type": "scope"}
    with pytest.raises(ContractViolation, match="not marked ask: scope"):
        validate({**GOOD, "question_candidates": [CANDIDATE, scoped]}, TASK)


def test_keep_and_rewrite_cannot_claim_an_open_gap():
    with pytest.raises(ContractViolation, match="cannot carry ask lenses"):
        validate({
            **GOOD,
            "decision": "KEEP",
            "question_candidates": [],
        }, TASK)


def test_silent_baseline_can_still_be_reproduced_explicitly():
    result = validate({**GOOD, "decision": None, "decision_reason": None}, TASK,
                      require_decision=False)
    assert len(result["question_candidates"]) == 1


# ── the real-model eval has executable expectations ──────────────────────────

def test_eval_expectations_accept_a_matching_review():
    result = validate(GOOD, TASK)
    expected = {
        "decisions": ["ASK"],
        "questions": {"min": 1, "max": 1},
        "rewrite": "forbidden",
        "required_question_concepts": [["storing", "database"]],
    }
    assert evaluation_problems(result, expected) == []


def test_eval_expectations_reject_wrong_action_and_question_content():
    result = validate(GOOD, TASK)
    result["decision_claimed"] = "KEEP"
    expected = {
        "decisions": ["ASK"],
        "questions": {"min": 2, "max": 2},
        "rewrite": "required",
        "forbidden_question_terms": ["application data"],
        "required_question_concepts": [["schema"]],
        "required_doubt_types": ["scope"],
    }
    problems = evaluation_problems(result, expected)
    assert any("decision KEEP" in problem for problem in problems)
    assert any("accepted 1 questions" in problem for problem in problems)
    assert any("rewrite instruction was required" in problem for problem in problems)
    assert any("forbidden term" in problem for problem in problems)
    assert any("required concept" in problem for problem in problems)
    assert any("required doubt type: scope" in problem for problem in problems)


def test_eval_forbidden_terms_match_words_and_phrases_not_substrings():
    result = validate(GOOD, TASK)
    expected = {
        "decisions": ["ASK"],
        "questions": {"min": 1, "max": 1},
        "forbidden_question_terms": ["go"],
    }
    assert evaluation_problems(result, expected) == []

    result["question_candidates"][0]["question"] = "What did you build in Go?"
    assert any("forbidden term 'go'" in problem
               for problem in evaluation_problems(result, expected))


def test_eval_expectations_count_validator_rejections_and_warnings():
    result = validate(
        {
            **GOOD,
            "question_candidates": [
                {**CANDIDATE, "question": "What was the impact of storing application data?"},
                {**CANDIDATE, "id": "c2", "question":
                 "How did Kafka change storing and managing application data?",
                 "requirement_reference": "r5"},
            ],
        },
        TASK,
    )
    problems = evaluation_problems(
        result,
        {"decisions": ["ASK"], "questions": {"min": 1, "max": 1}},
    )
    assert any("hard-rejected 1" in problem for problem in problems)
    assert any("carried warnings" in problem for problem in problems)


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
    assert any(str(MAX_QUESTION_CANDIDATES) in drop["why"] for drop in result["hard_rejected"])


def test_a_bullet_the_review_left_out_is_not_a_review():
    for nothing in (None, {}, "reviewed"):
        result = validate(nothing, TASK)
        assert result["decision"] == REVIEW_UNAVAILABLE
        assert result["question_candidates"] == []
        assert result["strength_assessment"] is None
        assert result["unavailable_reason"]


def test_one_bullet_review_is_bound_by_the_server_not_the_models_echo(monkeypatch):
    """The paid eval showed valid calls disappearing when the model failed to repeat ``b1``."""
    monkeypatch.setattr(
        "services.bullet_review_v2.request_review",
        lambda *_args, **_kwargs: [{**GOOD, "bullet": "the target bullet text"}],
    )

    result = review(("Backend Engineer", "Acme", "", []), [TASK])

    assert result["b"]["decision_claimed"] == "ASK"
    assert result["b"]["question_candidates"][0]["id"] == "c1"


def test_one_bullet_review_reports_an_empty_model_result(monkeypatch):
    monkeypatch.setattr(
        "services.bullet_review_v2.request_review",
        lambda *_args, **_kwargs: [],
    )

    result = review(("Backend Engineer", "Acme", "", []), [TASK])

    assert result["b"]["decision"] == REVIEW_UNAVAILABLE
    assert result["b"]["unavailable_reason"] == "the model returned no review entry"


def test_extra_reviews_use_the_unique_target_key_not_position_or_decision(monkeypatch):
    extra = {**GOOD, "bullet": "b2", "decision": "KEEP", "question_candidates": [],
             "gap_scan": {key: "settled" for key in GAP_SCAN}}
    monkeypatch.setattr(reviewer, "request_review", lambda *a, **k: [extra, GOOD])
    result = review(("Backend", "Acme", "", []), [TASK])["b"]
    assert result["decision_claimed"] == "ASK"
    assert result["question_candidates"][0]["question"] == CANDIDATE["question"]
    assert result["ignored_review_entries"] == 1


@pytest.mark.parametrize("keys", [("b2", "b3"), ("b1", "b1")])
def test_extra_reviews_without_a_unique_target_remain_unavailable(monkeypatch, keys):
    monkeypatch.setattr(reviewer, "request_review", lambda *a, **k: [
        {**GOOD, "bullet": key} for key in keys
    ])
    result = review(("Backend", "Acme", "", []), [TASK])["b"]
    assert result["decision"] == REVIEW_UNAVAILABLE


def test_extra_reviews_do_not_bypass_target_validation(monkeypatch):
    monkeypatch.setattr(reviewer, "request_review", lambda *a, **k: [
        {**GOOD, "decision": "KEEP"}, {**GOOD, "bullet": "b2"},
    ])
    with pytest.raises(ContractViolation, match="KEEP carries"):
        review(("Backend", "Acme", "", []), [TASK])


def test_one_bullet_review_accepts_an_unwrapped_review_object(monkeypatch):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(GOOD),
    ))])
    monkeypatch.setattr("services.openai_services.complete_json", lambda *_a, **_k: response)

    result = review(("Backend Engineer", "Acme", "", []), [TASK])

    assert result["b"]["decision_claimed"] == "ASK"
    assert result["b"]["question_candidates"][0]["id"] == "c1"


def test_one_bullet_call_sends_and_retries_the_direct_contract(monkeypatch):
    captured = []
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps(GOOD),
    ))])

    def complete(messages, **_kwargs):
        captured.append(messages)
        return response

    monkeypatch.setattr("services.openai_services.complete_json", complete)
    raw = reviewer.request_review(
        ("Backend Engineer", "Acme", "", []), [TASK], correction="wrong shape",
    )

    assert raw == [GOOD]
    request = json.loads(captured[0][1]["content"])
    assert set(request) == {"job_description", "target"}
    assert "bullet" not in request["target"]
    correction = captured[0][-1]["content"]
    assert "one review object directly" in correction
    assert "Do not return a reviews array" in correction


def test_retry_explains_an_empty_review_and_recovers(monkeypatch):
    calls = []

    def request(*_args, **kwargs):
        calls.append(kwargs.get("correction"))
        return [] if len(calls) == 1 else [{**GOOD, "decision": "KEEP",
                                            "decision_reason": "The contribution is concrete.",
                                            "gap_scan": {key: "settled" for key in GAP_SCAN},
                                            "question_candidates": []}]

    monkeypatch.setattr(reviewer, "request_review", request)
    result = reviewer.review_bullets(
        ("Security Engineer", "Acme", "", []), [TASK], concurrency=1,
    )

    assert calls[0] is None
    assert calls[1] == "the model returned no review entry"
    assert result["b"]["decision_claimed"] == "KEEP"


def test_two_empty_reviews_preserve_the_specific_failure_reason(monkeypatch):
    monkeypatch.setattr(reviewer, "request_review", lambda *_a, **_k: [])

    result = reviewer.review_bullets(
        ("Security Engineer", "Acme", "", []), [TASK], concurrency=1,
    )

    assert result["b"]["decision"] == REVIEW_UNAVAILABLE
    assert result["b"]["unavailable_reason"] == "the model returned no review entry"


def test_review_worker_expands_a_single_ask_candidate(monkeypatch):
    triage = {**GOOD, "question_candidates": [], "uncertainties": [GAP]}
    monkeypatch.setattr(reviewer, "request_review", lambda *_a, **_k: [triage])
    monkeypatch.setattr(reviewer, "request_alternatives", lambda *_a, **_k: [{
        "question": "Which storage operations did you personally implement?",
        "missing_fact": "the data stored in PostgreSQL",
        "recruiter_doubt_type": "contribution", "uncertainty_id": "g1",
        "why_it_matters_for_this_job": "The role owns backend data boundaries.",
        "expected_resume_change": "The bullet could name the records the database stored.",
        "priority": "medium",
        "requirement_reference": "r2",
    }])

    result = reviewer.review_bullets(
        ("Backend Engineer", "Acme", "", ["postgresql"]), [TASK], concurrency=1,
    )

    assert [item["id"] for item in result["b"]["question_candidates"]] == ["c1"]


def test_failed_question_generation_does_not_turn_an_ask_into_keep(monkeypatch):
    triage = {**GOOD, "question_candidates": [], "uncertainties": [GAP]}
    monkeypatch.setattr(reviewer, "request_review", lambda *_a, **_k: [triage])

    def unavailable(*_args, **_kwargs):
        raise reviewer.ReviewUnavailable("alternative call failed")

    monkeypatch.setattr(reviewer, "request_alternatives", unavailable)
    result = reviewer.review_bullets(
        ("Backend Engineer", "Acme", "", ["postgresql"]), [TASK], concurrency=1,
    )

    assert result["b"]["decision"] == REVIEW_UNAVAILABLE
    assert result["b"]["unavailable_kind"] == "question_generation"
    assert "generated no usable question" in result["b"]["unavailable_reason"]


def test_all_safely_rejected_candidates_preserve_the_review_as_nonfatal_unavailable(monkeypatch):
    triage = {**GOOD, "question_candidates": [], "uncertainties": [GAP]}
    monkeypatch.setattr(reviewer, "request_review", lambda *_a, **_k: [triage])
    monkeypatch.setattr(reviewer, "request_alternatives", lambda *_a, **_k: [{
        **CANDIDATE,
        "uncertainty_id": "wrong-gap",
    }])

    result = reviewer.review_bullets(
        ("Backend Engineer", "Acme", "", []), [TASK], concurrency=1,
    )["b"]

    assert result["decision"] == REVIEW_UNAVAILABLE
    assert result["unavailable_kind"] == "question_candidates_rejected"
    assert result["offered"] == 1
    assert result["hard_rejected"][0]["why"] == (
        "question does not match an approved uncertainty"
    )
    assert result["review_before_unavailable"]["decision_claimed"] == "ASK"


def test_question_skill_check_accepts_source_word_forms_and_seeded_implications():
    task = {
        **TASK,
        "text": (
            "Deployed Dockerized services on Railway with GitHub Actions and 772 automated "
            "tests, including backend tests against PostgreSQL."
        ),
    }
    candidate = {
        **CANDIDATE,
        "question": "What tasks did you perform during deployment and testing?",
    }

    assert reviewer.unsupported_question_skills(candidate["question"], task) == []
    assert reviewer._hard_problem(candidate, task, set(), set()) is None

    invented = "Which Redis deployment mechanism did you configure?"
    assert reviewer.unsupported_question_skills(invented, task) == ["redis"]


# ── the fit context is derived from the assessment, never from labels ─────────

def test_fit_context_separates_the_five_kinds():
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
        {"requirement": "redis", "state": "PARTIAL", "importance": "required",
         "inferred_from": ["database"], "evidence": [{"bullet_id": "bullet-1"}]},
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
    partial_ids = {item["id"] for item in context["related_partial"]}
    claimed_ids = {item["id"] for item in context["claimed_not_demonstrated"]}
    assert "r2" in gap_ids
    assert partial_ids == {"r5"}
    assert context["related_partial"][0]["inferred_from"] == ["database"]
    assert not referenceable({**TASK, **context}) & (gap_ids | partial_ids | claimed_ids)


def test_triage_requires_a_real_source_and_a_precise_uncertainty():
    raw = {**GOOD, 'question_candidates': [], 'uncertainties': [GAP]}
    assert validate(raw, TASK, triage_only=True)['uncertainties'] == [GAP]
    with pytest.raises(ContractViolation, match='precise uncertainty'):
        validate({**raw, 'uncertainties': []}, TASK, triage_only=True)
    with pytest.raises(ContractViolation, match='evidence_quote'):
        validate({**raw, 'uncertainties': [{**GAP, 'evidence_quote': 'Led a team'}]},
                 TASK, triage_only=True)


def test_triage_accepts_one_fact_spanning_two_lenses_and_punctuation_only_quote_change():
    target = {
        **TASK,
        "text": (
            "Made multi-minute tailoring runs resilient by moving execution to a worker with "
            "leases and checkpoints; verified recovery by killing a worker mid-run."
        ),
    }
    uncertainty = {
        **GAP,
        "evidence_quote": (
            "Made multi-minute tailoring runs resilient by moving execution to a worker with "
            "leases and checkpoints."
        ),
    }
    raw = {
        **GOOD,
        "gap_scan": {**GAP_SCAN, "implementation": "ask"},
        "question_candidates": [],
        "uncertainties": [uncertainty],
    }

    result = validate(raw, target, triage_only=True)

    assert result["uncertainties"] == [uncertainty]
    assert result["contract_repairs"] == []


def test_triage_repairs_ask_as_type_only_when_the_lens_is_unambiguous():
    raw = {
        **GOOD,
        "gap_scan": {key: "settled" for key in GAP_SCAN} | {"clarification": "ask"},
        "question_candidates": [],
        "uncertainties": [{**GAP, "recruiter_doubt_type": "ask"}],
    }

    result = validate(raw, TASK, triage_only=True)

    assert result["uncertainties"][0]["recruiter_doubt_type"] == "clarification"
    assert result["contract_repairs"] == [
        "uncertainty g1: recruiter_doubt_type ask -> clarification"
    ]

    ambiguous = {
        **raw,
        "gap_scan": {**raw["gap_scan"], "contribution": "ask"},
    }
    with pytest.raises(ContractViolation, match="use an ask lens"):
        validate(ambiguous, TASK, triage_only=True)
    prompt = prompt_for(triage_only=True)
    assert 'contribution: which work' in prompt
    assert 'you propose the questions worth asking' not in prompt


def test_triage_restores_missing_ask_lens_from_typed_uncertainty():
    raw = {
        **GOOD,
        "gap_scan": {key: "settled" for key in GAP_SCAN},
        "question_candidates": [],
        "uncertainties": [GAP],
    }

    result = validate(raw, TASK, triage_only=True)

    assert result["gap_scan"][GAP["recruiter_doubt_type"]] == "ask"
    assert result["contract_repairs"] == [
        "gap_scan: restored ask lens(es) from typed uncertainties: contribution"
    ]


def test_triage_uses_complete_settled_scan_when_ask_has_no_uncertainty():
    base = {
        **GOOD,
        "gap_scan": {key: "settled" for key in GAP_SCAN},
        "question_candidates": [],
    }

    result = validate({**base, "uncertainties": []}, TASK, triage_only=True)

    assert result["decision_claimed"] == "KEEP"
    assert result["contract_repairs"] == [
        "decision: ASK -> KEEP because gap_scan has no ask lens or uncertainty"
    ]


def test_triage_cannot_invent_an_ask_lens_from_an_invalid_uncertainty():
    base = {
        **GOOD,
        "gap_scan": {key: "settled" for key in GAP_SCAN},
        "question_candidates": [],
    }

    with pytest.raises(ContractViolation, match="ASK requires at least one ask lens"):
        validate({
            **base,
            "uncertainties": [{**GAP, "recruiter_doubt_type": "not-a-lens"}],
        }, TASK, triage_only=True)


def test_generator_cannot_silently_escalate_a_settled_lens(monkeypatch):
    raw = {**GOOD, 'question_candidates': [], 'uncertainties': [GAP]}
    reviewed = validate(raw, TASK, triage_only=True)
    monkeypatch.setattr(reviewer, 'request_alternatives', lambda *a, **k: [
        {**CANDIDATE, 'uncertainty_id': 'g1', 'recruiter_doubt_type': 'scope'}])
    expanded = reviewer.expand_review(('Role', 'Co', '', []), TASK, reviewed)
    assert expanded['question_candidates'] == []
    assert expanded['gap_scan']['scope'] == GAP_SCAN['scope']
    assert 'approved uncertainty' in expanded['hard_rejected'][0]['why']


def test_explicit_reconsideration_is_kept_but_empty_generation_is_unavailable(monkeypatch):
    raw = {**GOOD, 'question_candidates': [], 'uncertainties': [GAP]}
    monkeypatch.setattr(reviewer, 'request_review', lambda *a, **k: [raw])
    monkeypatch.setattr(reviewer, 'request_alternatives', lambda *a, **k: {
        'resolution': 'no_question', 'resolution_reason': 'The supplied answer identifies the storage operations.',
        'resolved_uncertainty_ids': ['g1'], 'question_candidates': [],
    })
    result = reviewer.review_bullets(('Role', 'Co', '', []), [TASK], concurrency=1)['b']
    assert result['decision_claimed'] == 'KEEP'
    assert result['triage_before_reconsideration']['decision_claimed'] == 'ASK'
    monkeypatch.setattr(reviewer, 'request_alternatives', lambda *a, **k: [])
    assert reviewer.review_bullets(('Role', 'Co', '', []), [TASK], concurrency=1)['b']['decision'] == REVIEW_UNAVAILABLE


def test_triage_ask_cannot_also_instruct_a_rewrite():
    with pytest.raises(ContractViolation, match='ASK cannot carry'):
        validate({**GOOD, 'question_candidates': [], 'uncertainties': [GAP],
                  'rewrite_from_existing_evidence': 'Rewrite the contribution.'}, TASK, triage_only=True)


def test_production_generation_uses_pool_ceiling_not_hidden_three(monkeypatch):
    triage = {**GOOD, 'question_candidates': [], 'uncertainties': [GAP]}
    monkeypatch.setattr(reviewer, 'request_review', lambda *a, **k: [triage])
    def generate(*args, **kwargs):
        assert kwargs['limit'] == reviewer.MAX_QUESTION_CANDIDATES == 20
        return [{**CANDIDATE, 'uncertainty_id': 'g1'}]
    monkeypatch.setattr(reviewer, 'request_alternatives', generate)
    result = reviewer.review_bullets(('Role', 'Co', '', []), [TASK], concurrency=1)['b']
    assert len(result['question_candidates']) == 1  # ceiling, not quota
