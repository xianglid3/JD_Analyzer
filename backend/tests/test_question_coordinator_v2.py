import json
from types import SimpleNamespace

import pytest

from services import question_coordinator_v2 as coordinator


def candidate(local_id="c1", question="What did you personally build to manage application data?",
              missing_fact="the database work they personally built", priority="high"):
    return {
        "id": local_id,
        "source_quote": "Worked with PostgreSQL",
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


def decisions(*items):
    return {"decisions": list(items)}


def select(key):
    return {"id": key, "action": "select", "reason": None, "duplicate_of": None}


def reject_decision(key, reason="low_value", duplicate_of=None):
    return {
        "id": key,
        "action": "reject",
        "reason": reason,
        "duplicate_of": duplicate_of,
    }


def reject(key, reason="low_value", duplicate_of=None):
    return {"id": key, "reason": reason, "duplicate_of": duplicate_of}


def test_collect_candidates_preserves_question_text_and_adds_stable_ids():
    original = {
        **candidate(),
        "focused_candidate_id": "source:q1",
        "finding_ids": ["clarity:b1:f1"],
        "evidence_ids": ["bullet:b1"],
    }
    result = flattened(bullet(candidates=[original]))
    assert result[0]["id"] == "b1:c1"
    assert result[0]["question"] == original["question"]
    assert result[0]["bullet_id"] == "b1"
    assert result[0]["bullet_position"] == 0
    assert result[0]["candidate_position"] == 0
    assert result[0]["source_quote"] == original["source_quote"]
    assert result[0]["finding_ids"] == ["clarity:b1:f1"]
    assert result[0]["evidence_ids"] == ["bullet:b1"]


def test_collect_candidates_refuses_duplicate_composite_ids():
    with pytest.raises(ValueError, match="duplicate coordinator candidate id"):
        flattened(bullet(candidates=[candidate(), candidate()]))


def test_selected_questions_are_resolved_from_input_not_model_text():
    candidates = flattened(bullet())
    raw = selection(["b1:c1"], [])
    raw["selected_questions"] = [{"id": "b1:c1", "question": "Invented replacement"}]
    result = coordinator.validate(raw, candidates)
    assert result["selected"][0]["question"] == candidates[0]["question"]


def test_decision_rows_are_normalized_to_the_stored_selection_shape():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    result = coordinator.validate(
        decisions(select("b1:c1"), reject_decision("b1:c2")), candidates,
    )
    assert result["selected_ids"] == ["b1:c1"]
    assert result["rejected"] == [reject("b1:c2")]


def test_legacy_two_list_results_remain_readable():
    candidates = flattened(bullet())
    assert coordinator.validate(selection(["b1:c1"], []), candidates)["selected_ids"] == [
        "b1:c1"
    ]


def test_decision_rows_cannot_repeat_an_id_or_put_metadata_on_a_selection():
    candidates = flattened(bullet())
    with pytest.raises(coordinator.ContractViolation, match="decision id appears twice"):
        coordinator.validate(decisions(select("b1:c1"), reject_decision("b1:c1")), candidates)
    with pytest.raises(coordinator.ContractViolation, match="may not set reason"):
        coordinator.validate(
            decisions({**select("b1:c1"), "reason": "low_value"}), candidates,
        )


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


def test_same_gap_rejections_across_doubt_types_are_normalized_to_low_value():
    implementation = {
        **candidate("c2", "How was application data stored?"),
        "recruiter_doubt_type": "implementation",
    }
    candidates = flattened(bullet(candidates=[candidate("c1"), implementation]))
    result = coordinator.validate(
        selection(
            ["b1:c1"],
            [reject("b1:c2", "lower_priority_same_gap", "b1:c1")],
        ),
        candidates,
    )
    assert result["rejected"] == [reject("b1:c2", "low_value")]


def test_non_duplicate_reason_discards_a_representative():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    result = coordinator.validate(
        selection(["b1:c1"], [reject("b1:c2", "low_value", "b1:c1")]), candidates,
    )
    assert result["rejected"] == [reject("b1:c2", "low_value")]
    assert result["contract_repairs"] == [
        "rejection b1:c2: removed duplicate_of from reason low_value"
    ]


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
    output = decisions(select("b1:c1"))
    content = json.dumps(output)
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


def test_focused_selection_emits_complete_stage_trace(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(decisions(select("b1:c1"))),
        ))],
        usage=None,
    )
    monkeypatch.setattr("services.openai_services.complete_json", lambda *_a, **_k: response)
    events = []

    result = coordinator.request_selection(
        ("Backend Engineer", "Globex", "Own backend systems", ["postgresql"]),
        flattened(bullet()), trace_callback=events.append, trace_scope="resume",
    )

    assert result["selected_ids"] == ["b1:c1"]
    assert len(events) == 1
    assert events[0]["stage"] == "coordinator_selection"
    assert events[0]["status"] == "completed"
    assert events[0]["raw_response"]
    assert events[0]["normalized"]["selected_ids"] == ["b1:c1"]


def test_selection_uses_full_pool_when_overlap_prepass_is_unavailable(monkeypatch):
    candidates = flattened(
        bullet("b1", [candidate("c1")]),
        bullet("b2", [candidate("c1", "What worker mechanism did you implement?")]),
    )
    monkeypatch.setattr(
        coordinator,
        "request_overlap",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("overlap timed out")),
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(decisions(
            select("b1:c1"), reject_decision("b2:c1", "low_value"),
        ))))],
        usage=None,
    )
    monkeypatch.setattr("services.openai_services.complete_json", lambda *_a, **_k: response)

    result = coordinator.request_selection(("Role", "Co", "", []), candidates)

    assert result["selected_ids"] == ["b1:c1"]
    assert result["rejected"] == [reject("b2:c1", "low_value")]
    assert "overlap prepass unavailable" in result["contract_repairs"][0]


def test_prompt_has_no_ownership_word_definitions():
    prompt = coordinator.SYSTEM_PROMPT
    assert "without assigning\nownership from a verb list" in prompt
    assert "A leading action verb is already a claim about the candidate" not in prompt
    assert "OWNERSHIP QUESTIONS" not in prompt


def test_prompt_selects_a_distinct_replacement_after_rejecting_project_overlap():
    prompt = coordinator.SYSTEM_PROMPT
    assert "Compare candidates within each entry as a set" in prompt
    assert "does not automatically silence that bullet" in prompt
    assert "select a non-overlapping replacement" in prompt
    assert "Never select a replacement merely to maintain a question count" in prompt
    assert "NESTED SIBLING EXAMPLE" in prompt
    assert "keep the more concrete subsystem question" in prompt
    assert "select the system-scope question" in prompt


def test_every_structured_output_prompt_explicitly_requests_json():
    # OpenAI rejects response_format=json_object before inference unless a message says JSON.
    assert "json" in coordinator.SYSTEM_PROMPT.lower()
    assert "json" in coordinator.OVERLAP_PROMPT.lower()


def test_coordinator_prompt_does_not_replace_missing_mechanism_with_generic_impact():
    prompt = " ".join(coordinator.SYSTEM_PROMPT.split())
    assert "never substitutes for a concrete clarity question" in prompt
    assert "result-validation candidate may also survive" in prompt


def test_overlap_contract_keeps_distinct_questions_and_points_duplicates_to_a_root():
    candidates = flattened(
        bullet("b1", [candidate("c1")]),
        bullet("b2", [candidate("c1", "What database functionality did you build?")]),
    )
    raw = decisions(
        {"id": "b1:c1", "duplicate_of": "b2:c1"},
        {"id": "b2:c1", "duplicate_of": None},
    )
    assert coordinator.validate_overlap(raw, candidates) == {"b1:c1": "b2:c1"}

    chained = decisions(
        {"id": "b1:c1", "duplicate_of": "b2:c1"},
        {"id": "b2:c1", "duplicate_of": "b1:c1"},
    )
    with pytest.raises(coordinator.ContractViolation, match="must not point"):
        coordinator.validate_overlap(chained, candidates)


def test_overlap_omissions_and_self_references_conservatively_remain_distinct():
    candidates = flattened(
        bullet("b1", [candidate("c1")]),
        bullet("b2", [candidate("c1", "What database functionality did you build?")]),
    )
    normalized, repairs = coordinator._normalize_overlap(
        decisions(
            {"id": "b1:c1", "duplicate_of": "b1:c1"},
            {"id": "unknown:c1", "duplicate_of": None},
        ),
        candidates,
    )

    assert normalized == {}
    assert repairs == [
        "overlap b1:c1: treated self-reference as distinct",
        "overlap unknown:c1: ignored unknown id",
        "overlap b2:c1: omitted, treated as distinct",
    ]


def test_overlap_is_oriented_toward_the_higher_priority_representative():
    candidates = flattened(
        bullet("b1", [candidate("c1", priority="medium")]),
        bullet("b2", [candidate("c1", priority="high")]),
    )
    assert coordinator.validate_overlap(
        decisions(
            {"id": "b1:c1", "duplicate_of": None},
            {"id": "b2:c1", "duplicate_of": "b1:c1"},
        ),
        candidates,
    ) == {"b1:c1": "b2:c1"}


def test_overlap_group_is_flattened_to_one_highest_priority_representative():
    candidates = flattened(
        bullet("b1", [candidate("c1", priority="low")]),
        bullet("b2", [candidate("c1", priority="high")]),
        bullet("b3", [candidate("c1", priority="medium")]),
    )
    assert coordinator.validate_overlap(
        decisions(
            {"id": "b1:c1", "duplicate_of": None},
            {"id": "b2:c1", "duplicate_of": "b1:c1"},
            {"id": "b3:c1", "duplicate_of": "b1:c1"},
        ),
        candidates,
    ) == {"b1:c1": "b2:c1", "b3:c1": "b2:c1"}


def test_request_selection_filters_same_answer_siblings_before_selecting(monkeypatch):
    ros_contribution = candidate(
        "c1",
        "What specific contributions did you make to the ROS 2 autonomous-driving stack?",
        "the candidate's personal ROS 2 contribution",
        "medium",
    )
    ros_scope = {
        **candidate(
            "c2",
            "What role did the cone-perception software play in the ROS 2 stack?",
            "the cone-perception software's role",
            "high",
        ),
        "recruiter_doubt_type": "scope",
    }
    point_cloud = candidate(
        "c1",
        "What specific contributions did you make to point-cloud filtering and ground removal?",
        "the point-cloud work personally implemented",
        "high",
    )
    candidates = flattened(
        {
            **bullet("ros", [ros_contribution, ros_scope]),
            "entry": "Panther Racing — Software Team Member",
            "text": "Contributing to a ROS 2 stack focused on cone-perception software.",
        },
        {
            **bullet("point", [point_cloud]),
            "entry": "Panther Racing — Software Team Member",
            "text": "Working on point-cloud filtering, ground removal, and cone validation.",
        },
    )
    calls = []

    def complete_json(messages, **_kwargs):
        calls.append(messages[0]["content"])
        if messages[0]["content"] == coordinator.OVERLAP_PROMPT:
            output = decisions(
                {"id": "ros:c1", "duplicate_of": "point:c1"},
                {"id": "point:c1", "duplicate_of": None},
            )
        else:
            sent = json.loads(messages[1]["content"])["question_candidates"]
            assert [item["id"] for item in sent] == ["ros:c2", "point:c1"]
            output = decisions(select("ros:c2"), select("point:c1"))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))]
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = coordinator.validate(
        coordinator.request_selection(("Role", "Co", "Summary", []), candidates),
        candidates,
    )

    assert len(calls) == 2
    assert result["selected_ids"] == ["ros:c2", "point:c1"]
    assert result["rejected"] == [
        reject("ros:c1", "lower_priority_same_gap", "point:c1")
    ]


def test_payload_does_not_label_ownership_from_words():
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
    detailed_list = {
        **concrete,
        "target_bullet": (
            "Implemented browser-side key exchange, derivation, authenticated encryption, key "
            "wrapping and rotation, and signatures using ECDH, HKDF, AES-GCM, and Ed25519."
        ),
        "question": (
            "What specific components did you personally implement for the key exchange and "
            "encryption processes?"
        ),
    }
    vague_list = {
        **concrete,
        "target_bullet": "Built features, fixes, and improvements.",
    }

    candidates = [concrete, short, vague, mechanism, detailed_list, vague_list]
    sent = coordinator.payload(("Role", "Co", "Summary", []), candidates)["question_candidates"]
    assert len(sent) == len(candidates)
    for original, item in zip(candidates, sent):
        assert item["target_bullet"] == original["target_bullet"]
        assert item["question"] == original["question"]
        assert "possible_redundant_ownership" not in item["wording_hints"]


def test_only_observation_questions_for_claimed_results_are_automatically_preserved():
    observed = flattened(bullet(
        "payments",
        [{
            **candidate(
                "c1",
                "What did you observe that showed the checkout flow improved?",
                "the observation supporting the claimed improvement",
                "medium",
            ),
            "recruiter_doubt_type": "result_validation",
        }],
        text="Helped improve the checkout flow for the web store backend.",
    ))[0]
    assert coordinator.automatically_preserve(observed)
    assert not coordinator.automatically_preserve({
        **observed,
        "question": "What was the impact of the checkout work?",
    })
    assert not coordinator.automatically_preserve({
        **observed,
        "target_bullet": "Migrated the checkout service to a new database.",
    })
    assert not coordinator.automatically_preserve({**observed, "priority": "low"})


def test_result_question_requires_model_selection(monkeypatch):
    candidates = flattened(bullet(
        "payments",
        [{
            **candidate(
                "c1",
                "What did you observe that showed the checkout flow improved?",
                "the observation supporting the claimed improvement",
                "medium",
            ),
            "recruiter_doubt_type": "result_validation",
        }],
        text="Helped improve the checkout flow for the web store backend.",
    ))
    monkeypatch.setattr(
        "services.openai_services.complete_json",
        lambda *a, **k: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(selection(["payments:c1"], []))))]),
    )
    result = coordinator.validate(
        coordinator.request_selection(("Role", "Co", "Summary", []), candidates), candidates,
    )
    assert result["selected_ids"] == ["payments:c1"]


def test_request_selection_sends_evidence_without_ownership_word_hints(monkeypatch):
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
        assert [item["id"] for item in sent] == ["owned:c1", "vague:c1"]
        assert all("possible_redundant_ownership" not in item["wording_hints"] for item in sent)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            selection(["vague:c1"], [reject("owned:c1", "low_value")])
        )))])

    monkeypatch.setattr(coordinator, "request_overlap", lambda *a, **k: {})
    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    raw = coordinator.request_selection(("Role", "Co", "Summary", []), candidates)
    result = coordinator.validate(raw, candidates)

    assert result["selected_ids"] == ["vague:c1"]
    assert result["rejected"] == [reject("owned:c1", "low_value")]


def test_coordinator_prompt_has_a_polished_bullet_veto_without_word_lists():
    assert "POLISHED-BULLET VETO" in coordinator.SYSTEM_PROMPT
    assert "materially change recruiter understanding" in coordinator.SYSTEM_PROMPT
    assert "ownership verb" not in coordinator.SYSTEM_PROMPT.lower()


def test_request_selection_repairs_a_selected_and_rejected_id(monkeypatch):
    candidates = flattened(bullet())
    responses = [
        decisions(select("b1:c1"), reject_decision("b1:c1", "duplicate", "b1:c1")),
        decisions(select("b1:c1")),
    ]
    messages_seen = []

    def complete_json(messages, **_kwargs):
        messages_seen.append(messages)
        content = json.dumps(responses.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = coordinator.validate(
        coordinator.request_selection(("Role", "Co", "Summary", []), candidates),
        candidates,
    )

    assert result["selected_ids"] == ["b1:c1"]
    assert len(messages_seen) == 2
    assert "Do not repeat an id" in messages_seen[1][-1]["content"]


def test_corrective_selection_ignores_only_an_impossible_self_duplicate_rejection():
    candidates = flattened(bullet())
    raw = decisions(
        select("b1:c1"),
        reject_decision("b1:c1", "duplicate", "b1:c1"),
    )

    with pytest.raises(coordinator.ContractViolation, match="appears twice"):
        coordinator.validate(raw, candidates)

    result = coordinator.validate(raw, candidates, allow_missing=True)
    assert result["selected_ids"] == ["b1:c1"]
    assert result["rejected"] == []
    assert result["contract_repairs"] == [
        "decision b1:c1: ignored impossible self-duplicate rejection after select"
    ]


def test_corrective_selection_does_not_guess_at_a_real_select_reject_conflict():
    candidates = flattened(bullet())
    raw = decisions(
        select("b1:c1"),
        reject_decision("b1:c1", "already_answered"),
    )

    with pytest.raises(coordinator.ContractViolation, match="appears twice"):
        coordinator.validate(raw, candidates, allow_missing=True)


def test_nonduplicate_rejection_discards_irrelevant_duplicate_pointer():
    candidates = flattened(bullet())
    raw = decisions({
        "id": "b1:c1",
        "action": "reject",
        "reason": "already_answered",
        "duplicate_of": "some-other-question",
    })

    result = coordinator.validate(raw, candidates)

    assert result["selected_ids"] == []
    assert result["rejected"] == [reject("b1:c1", "already_answered")]
    assert result["contract_repairs"] == [
        "rejection b1:c1: removed duplicate_of from reason already_answered"
    ]


def test_request_selection_conservatively_rejects_an_id_omitted_twice(monkeypatch):
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    responses = [decisions(select("b1:c1")), decisions(select("b1:c1"))]
    calls = []

    def complete_json(messages, **_kwargs):
        calls.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(responses.pop(0)))
        )])

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = coordinator.validate(
        coordinator.request_selection(("Role", "Co", "Summary", []), candidates),
        candidates,
    )

    assert len(calls) == 2
    assert result["selected_ids"] == ["b1:c1"]
    assert result["rejected"] == [reject("b1:c2", "not_selected")]


def test_request_selection_merges_a_corrective_patch_with_prior_valid_rows(monkeypatch):
    candidates = flattened(
        bullet("b1", [candidate("c1"), candidate("c2")]),
        bullet("b2", [candidate("c1")]),
    )
    responses = [
        decisions(select("b1:c1"), select("b2:c1")),
        decisions(reject_decision(
            "b1:c2", "lower_priority_same_gap", "b1:c1",
        )),
    ]

    monkeypatch.setattr(coordinator, "request_overlap", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "services.openai_services.complete_json",
        lambda *_a, **_k: SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(responses.pop(0)))
        )]),
    )

    result = coordinator.request_selection(("Role", "Co", "Summary", []), candidates)

    assert result["selected_ids"] == ["b1:c1", "b2:c1"]
    assert result["rejected"] == [
        reject("b1:c2", "lower_priority_same_gap", "b1:c1"),
    ]


def test_corrective_merge_removes_a_stale_duplicate_edge_without_losing_prior_rows(
        monkeypatch):
    candidates = flattened(
        bullet("b1", [candidate("c1"), candidate("c2")]),
        bullet("b2", [candidate("c1"), candidate("c2")]),
    )
    responses = [
        decisions(select("b1:c1"), select("b2:c1")),
        decisions(
            reject_decision("b1:c2", "duplicate", "b1:c1"),
            reject_decision("b2:c2", "duplicate", "b2:missing"),
        ),
    ]

    monkeypatch.setattr(coordinator, "request_overlap", lambda *_a, **_k: {})
    monkeypatch.setattr(
        "services.openai_services.complete_json",
        lambda *_a, **_k: SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(responses.pop(0)))
        )]),
    )

    result = coordinator.request_selection(("Role", "Co", "Summary", []), candidates)

    assert result["selected_ids"] == ["b1:c1", "b2:c1"]
    assert result["rejected"] == [
        reject("b1:c2", "duplicate", "b1:c1"),
        reject("b2:c2", "not_selected"),
    ]


def test_invalid_duplicate_representative_remains_strict_without_merge_repair():
    candidates = flattened(bullet(candidates=[candidate("c1"), candidate("c2")]))
    raw = selection(
        ["b1:c1"],
        [reject("b1:c2", "duplicate", "b1:missing")],
    )

    with pytest.raises(coordinator.ContractViolation, match="selected representative"):
        coordinator.validate(raw, candidates, allow_missing=True)

    repaired = coordinator.validate(
        raw,
        candidates,
        allow_missing=True,
        repair_invalid_representatives=True,
    )
    assert repaired["rejected"] == [reject("b1:c2", "not_selected")]
    assert repaired["contract_repairs"] == [
        "rejection b1:c2: replaced invalid duplicate representative b1:missing "
        "with not_selected"
    ]


def test_model_can_reject_all_candidates_after_reading_them(monkeypatch):
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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(selection([], [reject("owned:c1", "low_value")]))))])

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


def test_team_ownership_question_is_not_automatically_rejected(monkeypatch):
    candidates = flattened(bullet('team', [candidate(question='Which parts did you personally implement?')],
        text='Built an ordering platform with a three-person team: Python services, PostgreSQL storage, and a React interface.'))
    seen = []
    def respond(messages, **kwargs):
        seen.extend(json.loads(messages[1]['content'])['question_candidates'])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(selection(['team:c1'], []))))])
    monkeypatch.setattr('services.openai_services.complete_json', respond)
    assert coordinator.request_selection(('Role', 'Co', '', []), candidates)['selected_ids'] == ['team:c1']
    assert seen[0]['target_bullet'] == candidates[0]['target_bullet']


def test_already_answered_result_can_be_rejected_despite_wording_hint(monkeypatch):
    candidates = flattened(bullet('worker', [{**candidate(question='What did you observe that showed recovery worked?'),
        'recruiter_doubt_type': 'result_validation'}],
        text='Improved resilience; verified recovery by killing a worker mid-run and resuming without duplicates.',
        answers=['Killed the worker and resumed without duplicates.']))
    def respond(messages, **kwargs):
        sent = json.loads(messages[1]['content'])['question_candidates'][0]
        assert sent['answers_given_in_this_run']
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(selection([], [reject('worker:c1', 'already_answered')]))))])
    monkeypatch.setattr('services.openai_services.complete_json', respond)
    result = coordinator.request_selection(('Role', 'Co', '', []), candidates)
    assert result['selected_ids'] == []


def test_coordinator_receives_findings_in_resume_order_not_completion_order():
    from services.tailoring_agent import _coordinator_bullets
    tasks = [{'bullet_id': key, 'text': key, 'resume_gaps': [{'id': 'r1'}]} for key in ['a', 'b']]
    review = {'decision_claimed': 'ASK', 'established_facts': ['known'], 'uncertainties': [{'id': 'g1'}],
              'question_candidates': [candidate()]}
    boundary = _coordinator_bullets(tasks, {'b': review, 'a': review})
    collected = coordinator.collect_candidates(boundary)
    assert [item['bullet_id'] for item in collected] == ['a', 'b']
    assert collected[0]['review_findings']['established_facts'] == ['known']
    assert collected[0]['fit_context']['resume_gaps'] == [{'id': 'r1'}]
