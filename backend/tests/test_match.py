from services.match import compute_match_score

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


