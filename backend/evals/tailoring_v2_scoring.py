"""Deterministic scoring for the paid V2 end-to-end tailoring evaluation.

The model judges prose; this module checks only fixture facts that can be stated in advance.
It deliberately does not call a question "good" because it contains certain words. The owner
still reads every selected question and final edit before enabling V2.
"""


def _text(value):
    return " ".join(str(value or "").lower().split())


def evaluate_bullet(actual, expected):
    """Return concrete mismatches between one observed bullet and its fixture contract."""
    problems = []
    action = actual.get("action")
    allowed = expected.get("actions") or []
    if action not in allowed:
        problems.append(f"action {action!r} is not one of {', '.join(allowed) or 'none'}")

    questions = actual.get("questions") or []
    bounds = expected.get("questions") or {}
    minimum, maximum = bounds.get("min", 0), bounds.get("max", 10)
    if len(questions) < minimum or len(questions) > maximum:
        problems.append(
            f"showed {len(questions)} questions; expected between {minimum} and {maximum}"
        )
    joined_questions = _text(" ".join(questions))
    for term in expected.get("forbidden_question_terms") or []:
        if _text(term) in joined_questions:
            problems.append(f"question contains forbidden premise or template {term!r}")
    for alternatives in expected.get("required_question_concepts") or []:
        choices = alternatives if isinstance(alternatives, list) else [alternatives]
        if not any(_text(choice) in joined_questions for choice in choices):
            problems.append("questions miss expected concept: " + " | ".join(choices))

    edits = actual.get("edits") or []
    if expected.get("edit_after_answer") and actual.get("answered") and not edits:
        problems.append("answers were supplied but no edit followed")
    if expected.get("no_edit_after_answer") and edits:
        problems.append("an unhelpful answer should have left the bullet unchanged")
    joined_edits = _text(" ".join(edits))
    for term in expected.get("required_edit_terms") or []:
        if _text(term) not in joined_edits:
            problems.append(f"final edit is missing required supported detail {term!r}")
    for term in expected.get("forbidden_edit_terms") or []:
        if _text(term) in joined_edits:
            problems.append(f"final edit contains forbidden claim {term!r}")

    if expected.get("strong_control") and action != "keep":
        problems.append("strong control received unnecessary work")
    if expected.get("irrelevant_control") and action != "keep":
        problems.append("irrelevant control received unnecessary work")
    return problems


def aggregate(records):
    """Metrics whose numerator and denominator are visible in the printed report."""
    def ratio(items, predicate):
        return {
            "hits": sum(1 for item in items if predicate(item)),
            "total": len(items),
        }

    strong = [item for item in records if item["expected"].get("strong_control")]
    irrelevant = [item for item in records if item["expected"].get("irrelevant_control")]
    vague = [item for item in records if item["expected"].get("vague_relevant")]
    answered = [item for item in records if item["actual"].get("answered")]
    selected = sum(len(item["actual"].get("questions") or []) for item in records)
    selected_on_expected_ask = sum(
        len(item["actual"].get("questions") or [])
        for item in records
        if "ask" in (item["expected"].get("actions") or [])
    )
    return {
        "strong_false_positives": ratio(strong, lambda item: item["actual"]["action"] != "keep"),
        "irrelevant_false_positives": ratio(
            irrelevant, lambda item: item["actual"]["action"] != "keep"
        ),
        "vague_question_coverage": ratio(vague, lambda item: item["actual"]["action"] == "ask"),
        "answer_to_edit": ratio(answered, lambda item: bool(item["actual"].get("edits"))),
        "selected_questions": selected,
        # This is intentionally not called usefulness. A deterministic fixture can say whether
        # a question targeted a bullet expected to need clarification; only a reader can judge
        # whether the wording and missing fact are genuinely useful. The previous metric counted
        # a good question as useless when a later edit missed one required word.
        "selected_on_expected_ask": {
            "hits": selected_on_expected_ask, "total": selected,
        },
        "problems": sum(len(item.get("problems") or []) for item in records),
    }
