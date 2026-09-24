from evals.tailoring_v2_scoring import aggregate, evaluate_bullet


def test_scoring_checks_both_bad_passes_and_good_rejections():
    expected = {
        "actions": ["ask"],
        "questions": {"min": 1, "max": 2},
        "required_question_concepts": [["personally", "yourself"]],
        "forbidden_question_terms": ["which technologies"],
        "edit_after_answer": True,
        "required_edit_terms": ["postgresql"],
        "forbidden_edit_terms": ["50%"],
    }
    good = {
        "action": "ask", "questions": ["What did you personally build?"],
        "answered": True, "edits": ["Built PostgreSQL storage for tailoring runs."],
    }
    assert evaluate_bullet(good, expected) == []

    bad = {
        "action": "ask", "questions": ["Which technologies did you use?"],
        "answered": True, "edits": ["Improved throughput 50%."],
    }
    problems = evaluate_bullet(bad, expected)
    assert any("forbidden premise" in problem for problem in problems)
    assert any("no edit" not in problem and "missing required" in problem for problem in problems)
    assert any("forbidden claim" in problem for problem in problems)


def test_controls_and_aggregate_are_counted_from_labeled_records():
    records = [
        {
            "expected": {"strong_control": True},
            "actual": {"action": "keep", "questions": [], "answered": False, "edits": []},
            "problems": [],
        },
        {
            "expected": {"vague_relevant": True, "actions": ["ask"]},
            "actual": {"action": "ask", "questions": ["What did you build?"],
                       "answered": True, "edits": ["Built the worker."]},
            "problems": [],
        },
    ]
    metrics = aggregate(records)
    assert metrics["strong_false_positives"] == {"hits": 0, "total": 1}
    assert metrics["vague_question_coverage"] == {"hits": 1, "total": 1}
    assert metrics["answer_to_edit"] == {"hits": 1, "total": 1}
    assert metrics["selected_questions"] == 1
    assert metrics["selected_on_expected_ask"] == {"hits": 1, "total": 1}


def test_question_target_metric_is_not_corrupted_by_a_later_edit_problem():
    records = [{
        "expected": {"vague_relevant": True, "actions": ["ask"]},
        "actual": {"action": "ask", "questions": ["What did you build?"],
                   "answered": True, "edits": []},
        "problems": ["answers were supplied but no edit followed"],
    }]

    assert aggregate(records)["selected_on_expected_ask"] == {"hits": 1, "total": 1}
