import json
import threading
from types import SimpleNamespace

import pytest

from services import focused_review as focused


JOB = ("Backend Engineer", "Example", "Build reliable backend systems", ["PostgreSQL"])


def task(text="Worked on database functionality using PostgreSQL."):
    return {
        "bullet_id": "b1", "entry_id": "entry-1", "entry": "Engineer — Example",
        "text": text, "siblings": [], "sibling_bullets": [], "answers": [],
        "supported_explicit": [{"id": "r0", "label": "PostgreSQL", "importance": "required"}],
        "related_inferred": [], "claimed_not_demonstrated": [],
        "related_partial": [], "resume_gaps": [],
    }


def finding(check="clarity", state="unknown"):
    return {
        "finding_id": f"{check}:b1:f1",
        "anchors": [{"evidence_id": "bullet:b1", "quote": "database functionality"}],
        "known": "The bullet names PostgreSQL.",
        "information_needed": "The database functionality implemented.",
        "evidence_state": state,
        "resolution_evidence_ids": ["bullet:b1"] if state == "resolved_in_context" else [],
        "resume_change": "Implemented [database operations] for [application workflow].",
        "job_requirement_ids": ["r0"] if check == "opportunity" else [],
    }


def review(check, signal="no", findings=None):
    return {"check": check, "bullets": [{
        "bullet_id": "b1", "signal": signal,
        "summary": "Focused result.", "findings": list(findings or []),
    }]}


def materiality(would_change=False, gap_kind="none", delta=""):
    return {
        "would_change_resume": would_change,
        "gap_kind": gap_kind,
        "evidence_already_present": "The bullet names its current action and scope.",
        "proposed_resume_delta": delta,
    }


def test_clarity_validation_distinguishes_a_real_gap_from_a_clean_no():
    payload = focused.entry_input(JOB, [task()], "clarity")
    clean = focused.validate_review(review("clarity"), "clarity", payload)
    assert clean["bullets"][0]["signal"] == "no"

    vague = focused.validate_review(
        review("clarity", "yes", [finding()]), "clarity", payload,
    )
    assert vague["bullets"][0]["findings"][0]["information_needed"].startswith("The database")

    bad = finding()
    bad["anchors"][0]["quote"] = "an API that was never mentioned"
    with pytest.raises(focused.ContractViolation, match="invalid evidence quote"):
        focused.validate_review(review("clarity", "yes", [bad]), "clarity", payload)

    repaired = finding()
    repaired["anchors"][0]["evidence_id"] = "bullet:mistyped-id"
    normalized = focused.validate_review(
        review("clarity", "yes", [repaired]), "clarity", payload,
    )
    assert normalized["bullets"][0]["findings"][0]["anchors"][0]["evidence_id"] == "bullet:b1"
    assert normalized["contract_repairs"]

    short_id = finding()
    short_id["finding_id"] = "1"
    normalized = focused.validate_review(
        review("clarity", "yes", [short_id]), "clarity", payload,
    )
    assert normalized["bullets"][0]["findings"][0]["finding_id"] == "clarity:b1:1"


def test_fresh_review_materiality_must_match_signal_and_assigned_stage():
    payload = focused.entry_input(JOB, [task()], "clarity")
    clean = review("clarity")
    clean["bullets"][0]["materiality_check"] = materiality()
    normalized = focused.validate_review(clean, "clarity", payload)
    assert normalized["bullets"][0]["materiality_check"]["gap_kind"] == "none"

    vague = review("clarity", "yes", [finding()])
    vague["bullets"][0]["materiality_check"] = materiality(
        True,
        "action_or_change",
        "Replace vague database work with implemented [database operation].",
    )
    normalized = focused.validate_review(vague, "clarity", payload)
    assert normalized["bullets"][0]["materiality_check"]["would_change_resume"] is True

    contradictory = review("clarity", "yes", [finding()])
    contradictory["bullets"][0]["materiality_check"] = materiality()
    with pytest.raises(focused.ContractViolation, match="contradicts its materiality"):
        focused.validate_review(contradictory, "clarity", payload)

    wrong_stage = review("clarity", "yes", [finding()])
    wrong_stage["bullets"][0]["materiality_check"] = materiality(
        True,
        "stated_claim_support",
        "Add support for [claimed result].",
    )
    with pytest.raises(focused.ContractViolation, match="wrong-stage gap"):
        focused.validate_review(wrong_stage, "clarity", payload)


def test_empty_negative_materiality_summary_is_recovered_from_exact_target():
    payload = focused.entry_input(JOB, [task()], "clarity")
    clean = review("clarity")
    clean["bullets"][0]["materiality_check"] = {
        **materiality(),
        "evidence_already_present": "",
    }

    normalized = focused.validate_review(clean, "clarity", payload)

    assert normalized["bullets"][0]["materiality_check"]["evidence_already_present"] == (
        "Worked on database functionality using PostgreSQL."
    )
    assert normalized["contract_repairs"] == [
        "clarity b1: copied target text into empty evidence summary"
    ]


def test_empty_positive_materiality_summary_still_fails():
    payload = focused.entry_input(JOB, [task()], "clarity")
    vague = review("clarity", "yes", [finding()])
    vague["bullets"][0]["materiality_check"] = {
        **materiality(
            True,
            "action_or_change",
            "Replace vague database work with implemented [database operation].",
        ),
        "evidence_already_present": "",
    }

    with pytest.raises(focused.ContractViolation, match="incomplete materiality"):
        focused.validate_review(vague, "clarity", payload)


def test_stored_review_without_materiality_remains_recoverable():
    payload = focused.entry_input(JOB, [task()], "clarity")
    normalized = focused.validate_review(review("clarity"), "clarity", payload)

    assert normalized["bullets"][0]["materiality_check"] is None


def test_yes_with_no_material_gap_retries_instead_of_silently_becoming_keep():
    payload = focused.entry_input(JOB, [task()], "clarity")
    raw = review("clarity", "yes")
    raw["bullets"][0]["materiality_check"] = materiality()

    with pytest.raises(focused.ContractViolation, match="YES.*no unresolved"):
        focused.validate_review(raw, "clarity", payload)


def test_clarity_semantic_noise_reaches_the_gate_but_cannot_move_a_sibling_gap():
    row = task()
    row["sibling_bullets"] = [{
        "bullet_id": "b2",
        "text": "Improved an unrelated worker outcome.",
    }]
    payload = focused.entry_input(JOB, [row], "clarity")

    result_finding = finding()
    result_finding["information_needed"] = "What outcome resulted from the database work?"
    result_finding["resume_change"] = "Add the [outcome] of the database work."
    # This is a semantic-quality error rather than malformed or ungrounded data. Keep it
    # reviewable by the independent gate instead of failing the entire run in deterministic
    # validation.
    normalized = focused.validate_review(
        review("clarity", "yes", [result_finding]), "clarity", payload,
    )
    assert normalized["bullets"][0]["findings"][0]["information_needed"].startswith(
        "What outcome"
    )
    assert "belong in an interview" in focused.CLARITY_GATE

    sibling_finding = finding()
    sibling_finding["anchors"] = [{
        "evidence_id": "bullet:b2",
        "quote": "unrelated worker outcome",
    }]
    with pytest.raises(focused.ContractViolation, match="must anchor its target bullet"):
        focused.validate_review(
            review("clarity", "yes", [sibling_finding]), "clarity", payload,
        )


def test_fresh_review_schema_and_prompts_encode_the_resume_delta_boundary():
    row_schema = focused.REVIEW_SCHEMA["properties"]["bullets"]["items"]
    assert "materiality_check" in row_schema["required"]
    assert "dedicated resume-specificity reviewer" in focused.CLARITY
    assert "EVIDENCE-ANSWERABILITY" in focused.QUESTION_GENERATOR
    assert "technical-interview question" in focused.CLARITY
    assert "Mandatory ASK controls" in focused.CLARITY
    assert "remove the technologies and the product/category name" in focused.CLARITY
    assert "concrete_resume_clause" in focused.CLARITY_GATE
    assert "Never ask what criteria define valid evidence" in focused.CLARITY_GATE
    assert "answered_by_context" in focused.CLARITY_GATE
    assert "first reviewer" in focused.CLARITY_GATE


def test_clarity_gate_judges_evidence_without_receiving_the_first_reviewers_finding():
    row = task("Built a concrete PostgreSQL data layer with locking and unique indexes.")
    row["sibling_bullets"] = [{"bullet_id": "b2", "text": "Sibling hypothesis bait."}]
    row["answers"] = ["A prior answer for this target remains admissible evidence."]
    gate_finding = finding()
    gate_finding["anchors"][0]["quote"] = "PostgreSQL data layer"
    clarity = focused.validate_review(
        review("clarity", "yes", [gate_finding]),
        "clarity",
        focused.entry_input(JOB, [row], "clarity"),
    )["bullets"][0]
    payload = focused.clarity_gate_input(JOB, row, clarity)
    assert "clarity_review" not in payload
    assert "clarity:b1:f1" not in json.dumps(payload)
    assert all(item["kind"] != "sibling_bullet" for item in payload["evidence"])
    assert any(item["kind"] == "answered_detail" for item in payload["evidence"])
    raw = {
        "bullet_id": "b1",
        "evidence_quote": "locking and unique indexes",
        "concrete_resume_clause": True,
        "missing_clause": "",
        "verdict": "sufficient",
        "reason": "interview_depth",
        "explanation": "The bullet already states the concrete database work.",
    }

    gate = focused.validate_clarity_gate(raw, payload)
    filtered = focused.apply_clarity_gate(clarity, gate)

    assert filtered["signal"] == "no"
    assert filtered["findings"] == []
    assert filtered["dismissed_findings"][0]["gate_reason"] == "interview_depth"

    raw["verdict"] = "material_gap"
    with pytest.raises(focused.ContractViolation, match="contradicts"):
        focused.validate_clarity_gate(raw, payload)


def test_clarity_gate_applies_one_material_gap_verdict_to_existing_findings_only():
    row = task("Worked on database functionality using psycopg2 and indexes.")
    clarity = focused.validate_review(
        review("clarity", "yes", [finding()]), "clarity",
        focused.entry_input(JOB, [row], "clarity"),
    )["bullets"][0]
    raw = {
        "bullet_id": "b1",
        "evidence_quote": "Worked on database functionality using psycopg2 and indexes.",
        "concrete_resume_clause": False,
        "missing_clause": "Implemented [unknown] database operation.",
        "verdict": "material_gap",
        "reason": "material_gap",
        "explanation": "The target names an area and tools but no implemented operation.",
    }

    gate = focused.validate_clarity_gate(
        raw, focused.clarity_gate_input(JOB, row, clarity),
    )
    filtered = focused.apply_clarity_gate(clarity, gate)

    assert filtered["signal"] == "yes"
    assert [item["finding_id"] for item in filtered["findings"]] == ["clarity:b1:f1"]
    assert filtered["findings"][0]["clarity_gate_decision"] == "material_gap"


def test_clarity_gate_repairs_a_paraphrased_quote_to_exact_target_evidence():
    row = task("Worked on database functionality using psycopg2 and indexes.")
    payload = focused.clarity_gate_input(JOB, row, {})
    raw = {
        "bullet_id": "b1",
        "evidence_quote": "I worked on the database with indexes",
        "concrete_resume_clause": False,
        "missing_clause": "Implemented [unknown] database operation.",
        "verdict": "material_gap",
        "reason": "material_gap",
        "explanation": "The target names an area and tools but no implemented operation.",
    }

    normalized = focused.validate_clarity_gate(raw, payload)

    assert normalized["evidence_quote"] == row["text"]
    assert normalized["contract_repairs"]


def test_fresh_clarity_yes_runs_the_independent_gate_before_generation(monkeypatch):
    calls = []

    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        calls.append(system)
        if "Assigned check: clarity." in system:
            raw = review("clarity", "yes", [finding()])
            raw["bullets"][0]["materiality_check"] = materiality(
                True,
                "action_or_change",
                "Replace vague work with [database operation].",
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))],
                usage=None,
            )
        if "Assigned check: claim_support." in system:
            raw = review("claim_support", "not_applicable")
            raw["bullets"][0]["materiality_check"] = materiality()
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))],
                usage=None,
            )
        if "independent resume-sufficiency gate" in system:
            raw = {
                "bullet_id": "b1",
                "evidence_quote": "database functionality",
                "concrete_resume_clause": True,
                "missing_clause": "",
                "verdict": "sufficient",
                "reason": "already_sufficient",
                "explanation": "The target already states the concrete database work.",
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))],
                usage=None,
            )
        pytest.fail("question generation must not run after the gate rejects every finding")

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    result = focused.review_bullets(JOB, [task()], on_stage=events.append, concurrency=3)

    assert result["b1"]["decision"] == "KEEP"
    assert any(event["stage"] == "clarity_gate" for event in events)
    assert any("independent resume-sufficiency gate" in prompt for prompt in calls)


def test_opportunity_input_excludes_resume_wide_gaps_and_global_job_keywords():
    row = task()
    row["resume_gaps"] = [
        {"id": "gap-security", "label": "security", "importance": "required"},
    ]
    row["related_partial"] = [{
        "id": "gap-redux", "label": "redux", "importance": "preferred",
        "inferred_from": ["javascript"],
    }]
    payload = focused.entry_input(
        ("Backend Engineer", "Example", "Build distributed security systems",
         ["security", "distributed systems"]),
        [row], "opportunity",
    )

    encoded = json.dumps(payload)
    assert "gap-security" not in encoded
    assert "gap-redux" not in encoded
    assert "distributed security systems" not in encoded
    assert payload["job_context"]["skills"] == []
    assert payload["job_context"]["requirements"] == []
    assert "resume_gaps" not in payload["fit_hints"][0]


def test_opportunity_input_keeps_only_unresolved_bullet_linked_fit_leads():
    row = task()
    row["claimed_not_demonstrated"] = [
        {"id": "r-claim", "label": "transaction recovery", "importance": "required",
         "bullet_id": "b1"},
    ]
    row["related_partial"] = [
        {"id": "r-partial", "label": "distributed systems", "importance": "preferred",
         "bullet_id": "b1"},
    ]
    payload = focused.entry_input(JOB, [row], "opportunity")

    assert {item["id"] for item in payload["job_context"]["requirements"]} == {
        "r-claim", "r-partial",
    }
    assert set(payload["job_context"]["skills"]) == {
        "transaction recovery", "distributed systems",
    }


def test_clarity_prompt_prioritizes_concrete_actions_and_boundaries():
    prompt = " ".join(focused.CLARITY.split())
    assert "operation, component, or boundary affected" in prompt
    assert "browser-cryptography question is resolved" in prompt


def test_generator_prompt_forbids_narrowing_a_multi_fact_finding():
    prompt = " ".join(focused.QUESTION_GENERATOR.split())
    assert "cover the complete information_needed and resume_change" in prompt
    assert "asking only for authentication does not resolve it" in prompt
    assert "asking only for the browser operation does not resolve it" in prompt


def test_valid_no_cannot_hide_an_unresolved_finding():
    payload = focused.entry_input(JOB, [task()], "clarity")
    with pytest.raises(focused.ContractViolation, match="NO.*unresolved"):
        focused.validate_review(review("clarity", "no", [finding()]), "clarity", payload)


def test_no_label_is_repaired_when_unresolved_findings_and_materiality_agree():
    payload = focused.entry_input(JOB, [task()], "clarity")
    raw = review("clarity", "no", [finding()])
    raw["bullets"][0]["materiality_check"] = materiality(
        True, "action_or_change", "Replace vague work with [database operation].",
    )

    normalized = focused.validate_review(raw, "clarity", payload)

    assert normalized["bullets"][0]["signal"] == "yes"
    assert "changed NO to YES" in normalized["contract_repairs"][0]


def test_yes_with_only_resolved_findings_repairs_to_no():
    payload = focused.entry_input(JOB, [task()], "claim_support")
    normalized = focused.validate_review(
        review("claim_support", "yes", [finding("claim_support", "resolved_in_context")]),
        "claim_support", payload,
    )

    assert normalized["bullets"][0]["signal"] == "no"
    assert normalized["bullets"][0]["findings"][0]["evidence_state"] == (
        "resolved_in_context"
    )
    assert normalized["contract_repairs"] == [
        "claim_support b1: changed YES to NO because every finding was resolved_in_context"
    ]


def test_resolved_finding_recovers_missing_resolution_id_from_exact_anchor():
    payload = focused.entry_input(JOB, [task()], "clarity")
    resolved = finding(state="resolved_in_context")
    resolved["resolution_evidence_ids"] = []

    normalized = focused.validate_review(
        review("clarity", "no", [resolved]), "clarity", payload,
    )

    assert normalized["bullets"][0]["findings"][0]["resolution_evidence_ids"] == [
        "bullet:b1"
    ]
    assert normalized["contract_repairs"] == [
        "finding clarity:b1:f1: copied exact anchors into missing resolution evidence"
    ]


def test_incomplete_resolved_no_gap_finding_is_removed_instead_of_failing_bullet():
    payload = focused.entry_input(JOB, [task()], "clarity")
    resolved = finding(state="resolved_in_context")
    resolved["resume_change"] = ""
    raw = review("clarity", "no", [resolved])
    raw["bullets"][0]["materiality_check"] = materiality()

    normalized = focused.validate_review(raw, "clarity", payload)

    assert normalized["bullets"][0]["signal"] == "no"
    assert normalized["bullets"][0]["findings"] == []
    assert normalized["contract_repairs"] == [
        "finding clarity:b1:f1: removed incomplete resolved no-gap finding"
    ]


def test_yes_without_any_finding_remains_unavailable():
    payload = focused.entry_input(JOB, [task()], "claim_support")
    with pytest.raises(focused.ContractViolation, match="YES.*no unresolved"):
        focused.validate_review(review("claim_support", "yes"), "claim_support", payload)


def test_generator_requires_every_finding_and_grounded_references():
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [finding()]), "clarity",
            focused.entry_input(JOB, [task()], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task()], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity"), "opportunity",
            focused.entry_input(JOB, [task()], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [task()], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": "What database functionality did you implement using PostgreSQL?",
            "information_needed": "The database functionality implemented.",
            "resume_change": "Implemented [database operations] for [application workflow].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It would turn a broad backend claim into concrete work.",
        }],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1", "disposition": "ask",
            "candidate_ids": ["b1:q1"], "existing_question_ids": [],
            "resolution_evidence_ids": [], "reason": "The fact remains unknown.",
        }],
        "unreviewed_gaps": [],
    }
    result = focused.validate_generator(raw, payload)
    assert result["candidates"][0]["question"].startswith("What database")

    recovered_raw = json.loads(json.dumps(raw))
    recovered_raw["candidates"] = []
    recovered_raw["finding_dispositions"][0]["candidate_ids"] = []
    source_finding = payload["review_results"][0]["bullets"][0]["findings"][0]
    original_needed = source_finding["information_needed"]
    source_finding["information_needed"] = (
        "What database functionality did you implement using PostgreSQL?"
    )
    recovered = focused.validate_generator(recovered_raw, payload)
    assert recovered["candidates"][0]["candidate_id"].startswith("recovered:")
    assert recovered["finding_dispositions"][0]["candidate_ids"] == [
        recovered["candidates"][0]["candidate_id"]
    ]
    assert "recovered its direct reviewed question" in recovered["contract_repairs"][0]
    source_finding["information_needed"] = original_needed

    payload["review_results"][0]["bullets"][0]["findings"][0][
        "evidence_state"
    ] = "resolved_in_context"
    payload["review_results"][0]["bullets"][0]["findings"][0][
        "resolution_evidence_ids"
    ] = ["bullet:b1"]
    resolved = focused.validate_generator(raw, payload)
    assert resolved["candidates"] == []
    assert resolved["finding_dispositions"][0]["disposition"] == "covered"
    assert any("dropped because its fact is resolved" in item
               for item in resolved["contract_repairs"])
    payload["review_results"][0]["bullets"][0]["findings"][0][
        "evidence_state"
    ] = "unknown"
    payload["review_results"][0]["bullets"][0]["findings"][0][
        "resolution_evidence_ids"
    ] = []

    raw["finding_dispositions"] = []
    with pytest.raises(focused.ContractViolation, match="did not dispose every finding"):
        focused.validate_generator(raw, payload)


def test_generator_cannot_reverse_a_kept_clarity_gap_without_separate_coverage():
    row = task()
    row["sibling_bullets"] = [{
        "bullet_id": "b2",
        "text": "Implemented database storage and transaction recovery.",
    }]
    clarity = focused.validate_review(
        review("clarity", "yes", [finding()]), "clarity",
        focused.entry_input(JOB, [row], "clarity"),
    )["bullets"][0]
    gate = {
        "bullet_id": "b1",
        "evidence_quote": "Worked on database functionality using PostgreSQL.",
        "concrete_resume_clause": False,
        "missing_clause": "Implemented [unknown] database operation.",
        "verdict": "material_gap",
        "reason": "material_gap",
        "explanation": "The target does not state the database action.",
    }
    clarity = focused.apply_clarity_gate(
        clarity,
        focused.validate_clarity_gate(
            gate, focused.clarity_gate_input(JOB, row, clarity),
        ),
    )
    assert clarity["findings"][0]["clarity_gate_decision"] == "material_gap"
    reviews = {
        "clarity": {"check": "clarity", "bullets": [clarity]},
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [row], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity"), "opportunity",
            focused.entry_input(JOB, [row], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [row], reviews)
    raw = {
        "candidates": [],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1",
            "disposition": "dismiss",
            "candidate_ids": [],
            "existing_question_ids": [],
            "resolution_evidence_ids": ["bullet:b1"],
            "reason": "The target is sufficient.",
        }],
        "unreviewed_gaps": [],
    }
    with pytest.raises(focused.ContractViolation, match="without new coverage"):
        focused.validate_generator(raw, payload)

    raw["finding_dispositions"][0].update({
        "disposition": "covered",
        "resolution_evidence_ids": ["bullet:b2"],
        "reason": "The sibling supplies the action.",
    })
    normalized = focused.validate_generator(raw, payload)
    assert normalized["finding_dispositions"][0]["disposition"] == "covered"


def test_generator_cannot_blend_independent_checks_into_one_question():
    row = task()
    clarity = focused.validate_review(
        review("clarity", "yes", [finding("clarity")]), "clarity",
        focused.entry_input(JOB, [row], "clarity"),
    )
    support_finding = finding("claim_support")
    support = focused.validate_review(
        review("claim_support", "yes", [support_finding]), "claim_support",
        focused.entry_input(JOB, [row], "claim_support"),
    )
    opportunity = focused.validate_review(
        review("opportunity"), "opportunity",
        focused.entry_input(JOB, [row], "opportunity"),
    )
    payload = focused.generator_input(
        JOB, [row], {"clarity": clarity, "claim_support": support,
                     "opportunity": opportunity},
    )
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1", "claim_support:b1:f1"],
            "question": "What did you implement, and what impact did it have?",
            "information_needed": "Implementation and impact.",
            "resume_change": "Add [implementation] and [impact].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It would add two facts.",
        }],
        "finding_dispositions": [
            {"finding_id": "clarity:b1:f1", "disposition": "ask",
             "candidate_ids": ["b1:q1"], "existing_question_ids": [],
             "resolution_evidence_ids": [], "reason": "Unknown."},
            {"finding_id": "claim_support:b1:f1", "disposition": "ask",
             "candidate_ids": ["b1:q1"], "existing_question_ids": [],
             "resolution_evidence_ids": [], "reason": "Unknown."},
        ],
        "unreviewed_gaps": [],
    }

    with pytest.raises(focused.ContractViolation, match="independent checks"):
        focused.validate_generator(raw, payload)


def test_generator_rejects_a_question_mark_appended_to_a_noun_phrase():
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [finding()]), "clarity",
            focused.entry_input(JOB, [task()], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task()], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity", "not_applicable"), "opportunity",
            focused.entry_input(JOB, [task()], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [task()], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": "Details on the database operations implemented?",
            "information_needed": "The database operation.",
            "resume_change": "Add [database operation].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It makes the work concrete.",
        }],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1", "disposition": "ask",
            "candidate_ids": ["b1:q1"], "existing_question_ids": [],
            "resolution_evidence_ids": [], "reason": "Unknown.",
        }],
        "unreviewed_gaps": [],
    }

    with pytest.raises(focused.ContractViolation, match="grammatical direct question"):
        focused.validate_generator(raw, payload)


def test_generator_repairs_missing_terminal_question_mark():
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [finding()]), "clarity",
            focused.entry_input(JOB, [task()], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task()], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity", "not_applicable"), "opportunity",
            focused.entry_input(JOB, [task()], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [task()], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": "What database operations did you implement",
            "information_needed": "The database operation.",
            "resume_change": "Add [database operation].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It makes the work concrete.",
        }],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1", "disposition": "ask",
            "candidate_ids": ["b1:q1"], "existing_question_ids": [],
            "resolution_evidence_ids": [], "reason": "Unknown.",
        }],
        "unreviewed_gaps": [],
    }

    assert focused.validate_generator(raw, payload)["candidates"][0]["question"].endswith("?")


def test_generator_removes_speculative_suggested_answers_from_a_direct_question():
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [finding()]), "clarity",
            focused.entry_input(JOB, [task()], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task()], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity", "not_applicable"), "opportunity",
            focused.entry_input(JOB, [task()], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [task()], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": (
                "What database operations did you implement, such as sharding or replication?"
            ),
            "information_needed": "The database operation.",
            "resume_change": "Add [database operation].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It makes the work concrete.",
        }],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1", "disposition": "ask",
            "candidate_ids": ["b1:q1"], "existing_question_ids": [],
            "resolution_evidence_ids": [], "reason": "Unknown.",
        }],
        "unreviewed_gaps": [],
    }

    result = focused.validate_generator(raw, payload)

    assert result["candidates"][0]["question"] == "What database operations did you implement?"
    assert result["contract_repairs"] == [
        "candidate b1:q1: removed a suggested answer example",
    ]


def test_generator_repairs_multiple_question_clauses_and_ignores_unknown_disposition():
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [finding()]), "clarity",
            focused.entry_input(JOB, [task()], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task()], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity", "not_applicable"), "opportunity",
            focused.entry_input(JOB, [task()], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [task()], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": (
                "What database operations did you implement? "
                "What application workflow did they support?"
            ),
            "information_needed": "The database operation and application workflow.",
            "resume_change": "Add [database operation] for [application workflow].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It makes the work concrete.",
        }],
        "finding_dispositions": [
            {
                "finding_id": "clarity:b1:f1", "disposition": "ask",
                "candidate_ids": ["1"], "existing_question_ids": [],
                "resolution_evidence_ids": [], "reason": "Unknown.",
            },
            {
                "finding_id": "b1", "disposition": "covered",
                "candidate_ids": [], "existing_question_ids": [],
                "resolution_evidence_ids": [], "reason": "Extra model echo.",
            },
        ],
        "unreviewed_gaps": [],
    }

    result = focused.validate_generator(raw, payload)

    assert result["candidates"][0]["question"].count("?") == 1
    assert "What application workflow" in result["candidates"][0]["question"]
    assert result["contract_repairs"] == [
        "candidate 1: joined multiple question clauses",
        "ignored disposition for unknown finding 'b1'",
    ]


def test_generator_restores_a_complete_direct_finding_when_wording_drops_half():
    row = task()
    clarity_finding = finding()
    clarity_finding["information_needed"] = (
        "What authentication method was used, and what data was stored for message persistence?"
    )
    reviews = {
        "clarity": focused.validate_review(
            review("clarity", "yes", [clarity_finding]), "clarity",
            focused.entry_input(JOB, [row], "clarity"),
        ),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [row], "claim_support"),
        ),
        "opportunity": focused.validate_review(
            review("opportunity", "not_applicable"), "opportunity",
            focused.entry_input(JOB, [row], "opportunity"),
        ),
    }
    payload = focused.generator_input(JOB, [row], reviews)
    raw = {
        "candidates": [{
            "candidate_id": "b1:q1", "bullet_id": "b1",
            "finding_ids": ["clarity:b1:f1"],
            "question": "What authentication method was used?",
            "information_needed": "The authentication method.",
            "resume_change": "Add [authentication method].",
            "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
            "value_reason": "It makes authentication concrete.",
        }],
        "finding_dispositions": [{
            "finding_id": "clarity:b1:f1", "disposition": "ask",
            "candidate_ids": ["b1:q1"], "existing_question_ids": [],
            "resolution_evidence_ids": [], "reason": "The fact remains unknown.",
        }],
        "unreviewed_gaps": [],
    }

    result = focused.validate_generator(raw, payload)
    assert result["candidates"][0]["question"] == clarity_finding["information_needed"]
    assert result["contract_repairs"] == [
        "candidate b1:q1: restored the complete source finding after the generated question narrowed it"
    ]


def test_independent_generator_results_namespace_reused_local_ids_before_merge():
    base = {
        "candidates": [{
            "candidate_id": "candidate_1", "bullet_id": "b1", "finding_ids": ["f1"],
        }],
        "finding_dispositions": [{
            "finding_id": "f1", "candidate_ids": ["candidate_1"],
        }],
        "unreviewed_gaps": [],
    }
    clarity = focused._namespace_generation(base, "clarity")
    support = focused._namespace_generation(base, "claim_support")
    combined = focused._combine_generation({
        "clarity": clarity, "claim_support": support,
    })

    assert [item["candidate_id"] for item in combined["candidates"]] == [
        "clarity:candidate_1", "claim_support:candidate_1",
    ]
    assert [item["candidate_ids"] for item in combined["finding_dispositions"]] == [
        ["clarity:candidate_1"], ["claim_support:candidate_1"],
    ]


def test_opportunity_without_a_linked_fit_lead_uses_no_model_call(monkeypatch):
    calls = []

    def complete_json(messages, **_kwargs):
        calls.append(messages[0]["content"])
        if "Assigned check: clarity" in messages[0]["content"]:
            data = review("clarity")
        elif "Assigned check: claim_support" in messages[0]["content"]:
            data = review("claim_support", "not_applicable")
        else:
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    result = focused.review_bullets(JOB, [task()], on_stage=events.append, concurrency=3)

    assert not any("Assigned check: opportunity" in prompt for prompt in calls)
    opportunity = next(event for event in events if event["stage"] == "opportunity")
    assert opportunity["settings"] == {"kind": "deterministic_review", "model_call": False}
    assert result["b1"]["decision"] == "KEEP"


def test_all_three_checks_run_before_generation(monkeypatch):
    calls = []

    def complete_json(messages, **kwargs):
        system = messages[0]["content"]
        body = json.loads(messages[1]["content"])
        if "Assigned check: clarity" in system:
            data = review("clarity", "yes", [finding()])
            stage = "clarity"
        elif "Assigned check: claim_support" in system:
            data = review("claim_support", "not_applicable")
            stage = "claim_support"
        elif "Assigned check: opportunity" in system:
            data = review("opportunity")
            stage = "opportunity"
        else:
            data = {
                "candidates": [{
                    "candidate_id": "b1:q1", "bullet_id": "b1",
                    "finding_ids": ["clarity:b1:f1"],
                    "question": "What database functionality did you implement using PostgreSQL?",
                    "information_needed": "The database functionality implemented.",
                    "resume_change": "Implemented [database operations] for [workflow].",
                    "evidence_ids": ["bullet:b1"], "job_requirement_ids": [],
                    "value_reason": "It makes the contribution concrete.",
                }],
                "finding_dispositions": [{
                    "finding_id": "clarity:b1:f1", "disposition": "ask",
                    "candidate_ids": ["b1:q1"], "existing_question_ids": [],
                    "resolution_evidence_ids": [], "reason": "Unknown.",
                }],
                "unreviewed_gaps": [],
            }
            stage = "question_generation"
            assert len(body["review_results"]) == 1
        calls.append((stage, kwargs.get("schema_name")))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    task_row = task()
    task_row["related_partial"] = [
        {"id": "r-fit", "label": "database recovery", "importance": "preferred",
         "bullet_id": "b1"},
    ]
    result = focused.review_bullets(JOB, [task_row], on_stage=events.append, concurrency=3)

    assert {stage for stage, _schema in calls} == {
        "clarity", "claim_support", "opportunity", "question_generation",
    }
    assert result["b1"]["decision"] == "ASK"
    assert result["b1"]["question_candidates"][0]["finding_ids"] == ["clarity:b1:f1"]
    assert len(events) == 4


def test_clarity_and_opportunity_see_siblings_while_claim_support_stays_target_only(
        monkeypatch):
    first = task("Worked on database functionality using PostgreSQL.")
    second = {**task("Implemented a retry worker."), "bullet_id": "b2"}
    for row in (first, second):
        row["related_partial"] = [
            {"id": "r-fit", "label": "database recovery", "importance": "preferred",
             "bullet_id": row["bullet_id"]},
        ]
    first["sibling_bullets"] = [{"bullet_id": "b2", "text": second["text"]}]
    second["sibling_bullets"] = [{"bullet_id": "b1", "text": first["text"]}]
    reviewed = []

    def complete_json(messages, **_kwargs):
        body = json.loads(messages[1]["content"])
        system = messages[0]["content"]
        if "Assigned check:" in system:
            assert len(body["target_bullet_ids"]) == 1
            bullet_id = body["target_bullet_ids"][0]
            check = (
                "clarity" if "Assigned check: clarity." in system else
                "claim_support" if "Assigned check: claim_support." in system else
                "opportunity"
            )
            expected_evidence = (
                {bullet_id} if check == "claim_support" else {"b1", "b2"}
            )
            assert {item["bullet_id"] for item in body["evidence"]} == expected_evidence
            reviewed.append((check, bullet_id))
            data = {"check": check, "bullets": [{
                "bullet_id": bullet_id,
                "signal": "not_applicable" if check == "claim_support" else "no",
                "summary": "No unresolved finding.", "findings": [],
            }]}
        else:
            assert {row["bullet_id"] for review_row in body["review_results"]
                    for row in review_row["bullets"]} == {"b1", "b2"}
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = focused.review_bullets(JOB, [first, second], concurrency=6)
    assert set(reviewed) == {(stage, bullet_id) for stage in focused.REVIEW_STAGES
                            for bullet_id in ("b1", "b2")}
    assert {bullet_id: row["decision"] for bullet_id, row in result.items()} == {
        "b1": "KEEP", "b2": "KEEP",
    }


def test_completed_stage_cache_avoids_repeating_paid_calls(monkeypatch):
    task_row = task()
    stage_results = {
        "clarity": focused.validate_review(
            review("clarity"), "clarity", focused.entry_input(JOB, [task_row], "clarity")),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task_row], "claim_support")),
        "opportunity": focused.validate_review(
            review("opportunity"), "opportunity",
            focused.entry_input(JOB, [task_row], "opportunity")),
    }
    generated = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
    cache = {("entry-1:b1", stage): value for stage, value in stage_results.items()}
    cache[("entry-1", "question_generation")] = generated
    monkeypatch.setattr(
        "services.openai_services.complete_json",
        lambda *_a, **_k: pytest.fail("completed stages must not repeat a paid call"),
    )
    result = focused.review_bullets(JOB, [task_row], stage_cache=cache)
    assert result["b1"]["decision"] == "KEEP"


def test_existing_evidence_becomes_rewrite_work_without_a_question():
    task_row = task()
    resolved = finding(state="resolved_in_context")
    stage_results = {
        "clarity": focused.validate_review(
            review("clarity", "no", [resolved]), "clarity",
            focused.entry_input(JOB, [task_row], "clarity")),
        "claim_support": focused.validate_review(
            review("claim_support", "not_applicable"), "claim_support",
            focused.entry_input(JOB, [task_row], "claim_support")),
        "opportunity": focused.validate_review(
            review("opportunity"), "opportunity",
            focused.entry_input(JOB, [task_row], "opportunity")),
    }
    generated = focused.validate_generator({
        "candidates": [],
        "finding_dispositions": [{
            "finding_id": resolved["finding_id"], "disposition": "use_existing",
            "candidate_ids": [], "existing_question_ids": [],
            "resolution_evidence_ids": ["bullet:b1"],
            "reason": "The entry already supplies the fact.",
        }],
        "unreviewed_gaps": [],
    }, focused.generator_input(JOB, [task_row], stage_results))

    result = focused.merged_reviews([task_row], stage_results, generated)["b1"]
    assert result["decision"] == "REWRITE"
    assert result["question_candidates"] == []
    assert "[database operations]" in result["rewrite_from_existing_evidence"]


def test_failed_check_stays_unavailable_instead_of_becoming_no(monkeypatch):
    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        if "Assigned check: clarity" in system:
            raise focused.ContractViolation("clarity could not be validated")
        check = "claim_support" if "Assigned check: claim_support" in system else "opportunity"
        data = review(check, "not_applicable" if check == "claim_support" else "no")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = focused.review_bullets(JOB, [task()], concurrency=3)["b1"]
    assert result["decision"] == "REVIEW_UNAVAILABLE"
    assert set(result["failed_stages"]) == {"clarity"}
    assert result["unavailable_kind"] == "focused_stage"


def test_failed_check_isolated_to_its_target_bullet(monkeypatch):
    first = task()
    second = {**task("Worked on database functionality using PostgreSQL."), "bullet_id": "b2"}

    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        bullet_id = payload["target_bullet_ids"][0]
        if "Assigned check: clarity" in system and bullet_id == "b1":
            raise focused.ContractViolation("clarity could not be validated")
        if "Assigned check: clarity" in system:
            data = {"check": "clarity", "bullets": [{
                "bullet_id": bullet_id, "signal": "no",
                "summary": "The work is clear.", "findings": [],
            }]}
        else:
            data = {"check": "claim_support", "bullets": [{
                "bullet_id": bullet_id, "signal": "not_applicable",
                "summary": "No result claim.", "findings": [],
            }]}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    result = focused.review_bullets(JOB, [first, second], concurrency=3)

    assert result["b1"]["decision"] == "REVIEW_UNAVAILABLE"
    assert result["b1"]["failed_stages"] == {
        "clarity": "clarity could not be validated",
    }
    assert result["b2"]["decision"] == "KEEP"


def test_failed_entry_generation_falls_back_to_each_bullet(monkeypatch):
    rows = [task(), {**task(), "bullet_id": "b2"}]

    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        target_ids = payload["target_bullet_ids"]
        if "Assigned check: clarity" in system:
            bullet_id = target_ids[0]
            data = {"check": "clarity", "bullets": [{
                "bullet_id": bullet_id, "signal": "yes", "summary": "Vague database work.",
                "findings": [{
                    **finding(), "finding_id": "f1",
                    "anchors": [{
                        "evidence_id": f"bullet:{bullet_id}",
                        "quote": "database functionality",
                    }],
                }],
            }]}
        elif "Assigned check: claim_support" in system:
            bullet_id = target_ids[0]
            data = {"check": "claim_support", "bullets": [{
                "bullet_id": bullet_id, "signal": "not_applicable",
                "summary": "No result claim.", "findings": [],
            }]}
        elif len(target_ids) > 1:
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        else:
            bullet_id = target_ids[0]
            finding_id = f"clarity:{bullet_id}:f1"
            data = {
                "candidates": [{
                    "candidate_id": "q1", "bullet_id": bullet_id,
                    "finding_ids": [finding_id],
                    "question": "What database functionality did you implement?",
                    "information_needed": "The database functionality implemented.",
                    "resume_change": "Add [database operation].",
                    "evidence_ids": [f"bullet:{bullet_id}"], "job_requirement_ids": [],
                    "value_reason": "It would make the work concrete.",
                }],
                "finding_dispositions": [{
                    "finding_id": finding_id, "disposition": "ask",
                    "candidate_ids": ["q1"], "existing_question_ids": [],
                    "resolution_evidence_ids": [], "reason": "Unknown.",
                }],
                "unreviewed_gaps": [],
            }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    result = focused.review_bullets(
        JOB, rows, on_stage=events.append, concurrency=3,
    )

    assert result["b1"]["decision"] == "ASK"
    assert result["b2"]["decision"] == "ASK"
    focused_ids = {
        result[bullet_id]["question_candidates"][0]["focused_candidate_id"]
        for bullet_id in ("b1", "b2")
    }
    assert len(focused_ids) == 2
    generation = [event for event in events if event["stage"] == "question_generation"]
    assert sum(event["status"] == "failed" for event in generation) == 2
    assert sum(event["status"] == "completed" for event in generation) == 2


def test_retry_trace_preserves_failed_and_successful_attempts(monkeypatch):
    attempts = {"clarity": 0}
    messages_seen = []

    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        if "Assigned check: clarity" in system:
            attempts["clarity"] += 1
            messages_seen.append(messages)
            data = {"wrong": "shape"} if attempts["clarity"] == 1 else review("clarity")
        elif "Assigned check: claim_support" in system:
            data = review("claim_support", "not_applicable")
        elif "Assigned check: opportunity" in system:
            data = review("opportunity")
        else:
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    result = focused.review_bullets(JOB, [task()], on_stage=events.append, concurrency=3)
    clarity = [event for event in events if event["stage"] == "clarity"]
    assert [(event["attempt"], event["status"]) for event in clarity] == [
        (1, "failed"), (2, "completed"),
    ]
    assert len(messages_seen[0]) == 2
    assert "previous response could not be used" in messages_seen[1][-1]["content"]
    assert result["b1"]["decision"] == "KEEP"


def test_focused_checks_are_concurrent_but_callbacks_stay_on_orchestrator(monkeypatch):
    barrier = threading.Barrier(3)
    caller = threading.current_thread().name
    provider_threads = set()
    callback_threads = set()

    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        if "Assigned check:" in system:
            provider_threads.add(threading.current_thread().name)
            barrier.wait(timeout=2)
            if "Assigned check: clarity." in system:
                data = review("clarity")
            elif "Assigned check: claim_support." in system:
                data = review("claim_support", "not_applicable")
            else:
                data = review("opportunity")
        else:
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    task_row = task()
    task_row["related_partial"] = [
        {"id": "r-fit", "label": "database recovery", "importance": "preferred",
         "bullet_id": "b1"},
    ]
    focused.review_bullets(
        JOB, [task_row], concurrency=3,
        on_stage=lambda _event: callback_threads.add(threading.current_thread().name),
    )
    assert len(provider_threads) == 3
    assert callback_threads == {caller}


def test_failed_generation_is_traced_and_cannot_become_keep(monkeypatch):
    def complete_json(messages, **_kwargs):
        system = messages[0]["content"]
        if "Assigned check: clarity" in system:
            data = review("clarity", "yes", [finding()])
        elif "Assigned check: claim_support" in system:
            data = review("claim_support", "not_applicable")
        elif "Assigned check: opportunity" in system:
            data = review("opportunity")
        else:
            data = {"candidates": [], "finding_dispositions": [], "unreviewed_gaps": []}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=None,
        )

    monkeypatch.setattr("services.openai_services.complete_json", complete_json)
    events = []
    result = focused.review_bullets(JOB, [task()], on_stage=events.append, concurrency=3)
    generation = [event for event in events if event["stage"] == "question_generation"]
    assert [event["status"] for event in generation] == ["failed", "failed"]
    assert result["b1"]["decision"] == "REVIEW_UNAVAILABLE"
    assert "question_generation:clarity" in result["b1"]["failed_stages"]
