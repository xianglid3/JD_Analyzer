import pytest
from services.match import compute_match, compute_match_score, normalize_skill

def test_empty_job_skill_match():
    assert compute_match_score([], ["python"]) is None

def test_empty_resume_skill_match():
    assert compute_match_score(["python"], []) == 0.0

def test_half_match():
    assert compute_match_score(["python", "sql"], ["python"]) == 50.0

def test_zero_match():
    assert compute_match_score(["sql"], ["python"]) == 0.0

def test_perfect_match():
    assert compute_match_score(["python"], ["python"]) == 100.0

def test_case_match():
    assert compute_match_score(["Python"], ["python"]) == 100.0

def test_normalized_variants():
    # React.js / React and js / JavaScript should collapse to the same skill
    assert compute_match_score(["React"], ["React.js"]) == 100.0
    assert compute_match_score(["JavaScript"], ["js"]) == 100.0

def test_dedupe_on_normalized_key():
    # React + React.js are one skill → denominator not inflated (1 of 2 distinct = 50%)
    assert compute_match_score(["React", "React.js", "SQL"], ["react"]) == 50.0

def test_removesuffix_not_substring():
    assert normalize_skill("React.js") == "react"        # trailing .js stripped
    assert normalize_skill("react.jsx") == "react.jsx"   # NOT mangled to "reactx"


def test_match_breakdown():
    result = compute_match(
        ["Python", "React.js", "Docker"],
        ["python", "React"],
    )

    assert result == {
        "score": 66.67,
        "matched": ["Python", "React.js"],
        "missing": ["Docker"],
    }


def test_match_breakdown_preserves_jd_display_names():
    result = compute_match(
        ["JavaScript", "REST API", "System Design"],
        ["js", "api"],
    )

    assert result["matched"] == ["JavaScript", "REST API"]
    assert result["missing"] == ["System Design"]


# ── one spelling per concept ─────────────────────────────────────────────────
# "object-oriented design" and "object oriented design" normalized to two different keys, so
# the learned-relation cache looked the same term up twice — two paid model calls for one
# concept — and an edge learned under one spelling was invisible to the other.

@pytest.mark.parametrize("spelling", [
    "object-oriented design", "object oriented design", "object_oriented_design",
    "Object-Oriented  Design",
])
def test_punctuation_does_not_create_a_second_concept(spelling):
    assert normalize_skill(spelling) == "object oriented design"


@pytest.mark.parametrize("spelling", ["front-end", "front end", "front_end"])
def test_an_alias_resolves_however_it_is_punctuated(spelling):
    assert normalize_skill(spelling) == "frontend"


def test_hyphenated_canonical_names_still_resolve_through_the_graph():
    """The tables are written with readable hyphens; the lookups must not care."""
    from services.skill_graph import evidence_for, implied_by, rewrite_implied_by

    assert "css" in implied_by("styled-components")
    assert "css" in implied_by("styled components")
    assert "machine learning" in implied_by("scikit-learn")
    assert "machine learning" in implied_by("sklearn")        # via the alias table
    assert rewrite_implied_by("tailwind") == ["css"]
    assert "tailwind" in evidence_for("css")


def test_a_skill_whose_name_is_only_punctuation_apart_matches_evidence():
    from services.skill_evidence import evaluate_requirement

    result = evaluate_requirement("object oriented programming", [
        {"bullet_id": "b1", "text": "Applied object-oriented programming across the codebase"},
    ])
    assert result["state"] == "EXPLICIT"
