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
