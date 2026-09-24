import json
from types import SimpleNamespace

import pytest

from services import question_coordinator_v2 as coordinator


def candidate(local_id="c1", question="What did you personally build to manage application data?",
              missing_fact="the database work they personally built", priority="high"):
    return {
        "id": local_id,
        "question": question,
        "missing_fact": missing_fact,
        "recruiter_doubt_type": "contribution",
        "why_it_matters_for_this_job": "The role owns backend data storage.",
        "expected_resume_change": "The bullet could name the database work they built.",
        "priority": priority,
        "requirement_reference": "r2",
    }


def bullet(bullet_id="b1", candidates=None, answers=None, text=None):
    return {
        "bullet_id": bullet_id,
        "entry": "JobMatcha — Developer",
        "text": text or "Worked with PostgreSQL to store and manage application data.",
        "siblings": ["Built the resume-tailoring worker."],
        "answers": answers or [],
        "question_candidates": candidates if candidates is not None else [candidate()],
    }


def flattened(*bullets):
    return coordinator.collect_candidates(list(bullets))


def selection(selected, rejected):
    return {"selected_ids": selected, "rejected": rejected}


def reject(key, reason="low_value", duplicate_of=None):
    return {"id": key, "reason": reason, "duplicate_of": duplicate_of}


def test_collect_candidates_preserves_question_text_and_adds_stable_ids():
    original = candidate()
    result = flattened(bullet(candidates=[original]))
    assert result[0]["id"] == "b1:c1"
    assert result[0]["question"] == original["question"]
    assert result[0]["bullet_id"] == "b1"
    assert result[0]["bullet_position"] == 0
    assert result[0]["candidate_position"] == 0


def test_collect_candidates_refuses_duplicate_composite_ids():
    with pytest.raises(ValueError, match="duplicate coordinator candidate id"):
        flattened(bullet(candidates=[candidate(), candidate()]))


def test_selected_questions_are_resolved_from_input_not_model_text():
    candidates = flattened(bullet())
    raw = selection(["b1:c1"], [])
    raw["selected_questions"] = [{"id": "b1:c1", "question": "Invented replacement"}]
    result = coordinator.validate(raw, candidates)
    assert result["selected"][0]["question"] == candidates[0]["question"]


@pytest.mark.parametrize(
    "raw, message",
    [
        (selection(["missing:c1"], []), "unknown selected id"),
        (selection(["b1:c1", "b1:c1"], []), "appears twice"),
        (selection(["b1:c1"], [reject("b1:c1")]), "both selected and rejected"),
        (selection([], [reject("b1:c1", "vibes")]), "invalid rejection reason"),
        (selection([], []), "did not account for"),
    ],
)
def test_invalid_selection_shapes_are_contract_violations(raw, message):
    with pytest.raises(coordinator.ContractViolation, match=message):
        coordinator.validate(raw, flattened(bullet()))


def test_unknown_extra_rejection_is_ignored_but_cannot_replace_real_accounting():
    candidates = flattened(bullet())
    result = coordinator.validate(
        selection(["b1:c1"], [reject("b1:c2")]), candidates,
    )
    assert result["selected_ids"] == ["b1:c1"]
    assert result["ignored_rejections"] == [{"id": "b1:c2", "reason": "low_value"}]

    with pytest.raises(coordinator.ContractViolation, match="did not account for"):
        coordinator.validate(selection([], [reject("b1:c2")]), candidates)


def test_every_candidate_must_be_accounted_for_once():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    result = coordinator.validate(
        selection(["b1:c1"], [reject("b1:c2")]),
        candidates,
    )
    assert result["selected_ids"] == ["b1:c1"]
    assert result["rejected"] == [reject("b1:c2")]


@pytest.mark.parametrize("reason", ["duplicate", "lower_priority_same_gap"])
def test_duplicate_reasons_require_a_selected_representative(reason):
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    with pytest.raises(coordinator.ContractViolation, match="selected representative"):
        coordinator.validate(
            selection(["b1:c1"], [reject("b1:c2", reason)]), candidates,
        )
    result = coordinator.validate(
        selection(["b1:c1"], [reject("b1:c2", reason, "b1:c1")]), candidates,
    )
    assert result["rejected"][0]["duplicate_of"] == "b1:c1"


def test_non_duplicate_reason_forbids_a_representative():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    with pytest.raises(coordinator.ContractViolation, match="may not set duplicate_of"):
        coordinator.validate(
            selection(["b1:c1"], [reject("b1:c2", "low_value", "b1:c1")]), candidates,
        )


def test_exact_duplicate_questions_cannot_both_be_selected():
    same = "What did you personally build to manage application data?"
    candidates = flattened(bullet("b1", [candidate("c1", same), candidate("c2", same)]))
    with pytest.raises(coordinator.ContractViolation, match="exact duplicate"):
        coordinator.validate(selection(["b1:c1", "b1:c2"], []), candidates)


def test_exact_wording_on_different_bullets_requires_contextual_judgment():
    same = "What did you personally build?"
    candidates = flattened(
        bullet("b1", [candidate("c1", same)]),
        bullet("b2", [candidate("c1", same)]),
    )
    result = coordinator.validate(selection(["b1:c1", "b2:c1"], []), candidates)
    assert result["selected_ids"] == ["b1:c1", "b2:c1"]


def test_similar_questions_on_different_bullets_are_not_deterministically_deleted():
    candidates = flattened(
        bullet("b1", [candidate("c1", "What did you personally build to manage application data?")]),
        bullet("b2", [candidate("c1", "What did you personally build to manage users and messages?")]),
    )
    result = coordinator.validate(selection(["b1:c1", "b2:c1"], []), candidates)
    assert result["selected_ids"] == ["b1:c1", "b2:c1"]


def test_per_bullet_limit_is_enforced_without_a_global_limit():
    eleven = [
        candidate(f"c{i}", f"What did you personally build for application data part {i}?")
        for i in range(11)
    ]
    candidates = flattened(bullet("b1", eleven))
    with pytest.raises(coordinator.ContractViolation, match="more than 10"):
        coordinator.validate(selection([item["id"] for item in candidates], []), candidates)

    many_bullets = flattened(*[
        bullet(f"b{i}", [candidate("c1", f"What did you personally build for system area {i}?")])
        for i in range(12)
    ])
    result = coordinator.validate(selection([item["id"] for item in many_bullets], []), many_bullets)
    assert len(result["selected_ids"]) == 12


def test_empty_input_costs_no_model_call(monkeypatch):
    monkeypatch.setattr(coordinator, "request_selection", lambda *args, **kwargs: pytest.fail())
    assert coordinator.coordinate(("Role", "Co", "Summary", []), []) == {
        "selected_ids": [], "selected": [], "rejected": [],
    }


def test_request_selection_parses_json(monkeypatch):
    content = json.dumps(selection(["b1:c1"], []))
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )

    def complete_json(messages, **kwargs):
        assert messages[0]["content"] == coordinator.SYSTEM_PROMPT
        sent = json.loads(messages[1]["content"])
        assert sent["question_candidates"][0]["id"] == "b1:c1"
        return response

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    assert coordinator.request_selection(
        ("Backend Engineer", "Globex", "Own backend systems", ["postgresql"]),
        flattened(bullet()),
    ) == selection(["b1:c1"], [])


def test_prompt_separates_redundant_ownership_from_missing_contribution():
    prompt = coordinator.SYSTEM_PROMPT
    assert "A leading action verb is already a claim about the candidate" in prompt
    assert "Built an LLM platform with server-enforced grounding" in prompt
    assert "Worked on the PostgreSQL backend using psycopg2" in prompt


def test_automatic_filter_is_narrow_to_redundant_ownership_questions():
    concrete = flattened(bullet(
        "owned",
        [candidate(
            "c1",
            "What specific components did you personally implement for the platform?",
        )],
        text=(
            "Built an LLM platform with server-enforced grounding: unsupported edits are "
            "rejected before storage."
        ),
    ))[0]
    short = {**concrete, "target_bullet": "Built an app."}
    vague = {**concrete, "target_bullet": "Worked on an LLM platform using Python."}
    mechanism = {**concrete, "recruiter_doubt_type": "implementation"}

    assert coordinator.automatic_rejection_reason(concrete) == "low_value"
    assert coordinator.automatic_rejection_reason(short) is None
    assert coordinator.automatic_rejection_reason(vague) is None
    assert coordinator.automatic_rejection_reason(mechanism) is None


def test_request_selection_filters_redundant_ownership_before_the_model(monkeypatch):
    candidates = flattened(
        bullet(
            "owned",
            [candidate(
                "c1",
                "What specific components did you personally implement for the platform?",
            )],
            text=(
                "Built an LLM platform with server-enforced grounding: unsupported edits are "
                "rejected before storage."
            ),
        ),
        bullet(
            "vague",
            [candidate(
                "c1",
                "What specific database functionality did you personally build or change?",
            )],
            text="Worked on PostgreSQL backend and database functionality.",
        ),
    )

    def complete_json(messages, **_kwargs):
        sent = json.loads(messages[1]["content"])["question_candidates"]
        assert [item["id"] for item in sent] == ["vague:c1"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            selection(["vague:c1"], [])
        )))])

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    raw = coordinator.request_selection(("Role", "Co", "Summary", []), candidates)
    result = coordinator.validate(raw, candidates)

    assert result["selected_ids"] == ["vague:c1"]
    assert result["rejected"] == [reject("owned:c1", "low_value")]


def test_request_selection_skips_model_when_policy_rejects_every_candidate(monkeypatch):
    candidates = flattened(bullet(
        "owned",
        [candidate(
            "c1",
            "What specific part of the reserve-before-spend idempotency did you implement?",
        )],
        text=(
            "Implemented reserve-before-spend idempotency for job-draft creation so duplicate "
            "requests cannot trigger duplicate LLM calls."
        ),
    ))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("the model must not run when policy rejects every candidate")

    monkeypatch.setattr("services.openai_services.complete_json", fail_if_called)
    result = coordinator.validate(
        coordinator.request_selection(("Role", "Co", "Summary", []), candidates),
        candidates,
    )

    assert result["selected_ids"] == []
    assert result["rejected"] == [reject("owned:c1", "low_value")]


def test_request_selection_reports_malformed_json(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"selected_ids": ["b1:c1"'))],
    )
    monkeypatch.setattr("services.openai_services.complete_json", lambda *a, **k: response)
    with pytest.raises(coordinator.ReviewUnavailable, match="not valid JSON"):
        coordinator.request_selection(("Role", "Co", "Summary", []), flattened(bullet()))


def test_evaluation_problems_checks_selection_and_rejection_reasons():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    result = coordinator.validate(
        selection(["b1:c1"], [reject("b1:c2", "low_value")]), candidates,
    )
    expected = {
        "selected": {"min": 2, "max": 2},
        "required_selected_ids": ["b1:c2"],
        "forbidden_selected_ids": ["b1:c1"],
        "required_selected_groups": [["b1:c2", "b1:c3"]],
        "required_rejections": {"b1:c2": "duplicate"},
        "required_rejection_groups": [{
            "ids": ["b1:c1", "b1:c2"],
            "reasons": ["duplicate"],
        }],
        "rejection_reason_counts": {"duplicate": {"min": 1, "max": 2}},
    }
    problems = coordinator.evaluation_problems(result, expected)
    assert any("selected 1" in problem for problem in problems)
    assert any("required question" in problem for problem in problems)
    assert any("forbidden question" in problem for problem in problems)
    assert any("expected duplicate" in problem for problem in problems)
    assert any("no expected rejection" in problem for problem in problems)
    assert any("rejected 0 as duplicate" in problem for problem in problems)
