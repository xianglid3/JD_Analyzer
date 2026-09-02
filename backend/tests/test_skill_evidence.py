"""Evidence-based requirement matching: states, evidence, and the score built from them."""

import pytest

from services.skill_evidence import (
    EXPLICIT,
    INFERRED,
    NONE,
    PARTIAL,
    evaluate_requirement,
    evaluate_requirements,
)


def skill(text, bullet_id="s1"):
    return {"bullet_id": bullet_id, "kind": "skill", "text": text}


def bullet(text, bullet_id="b1"):
    return {"bullet_id": bullet_id, "kind": "project", "text": text}


RESUME = [
    skill("Tailwind", "s1"),
    skill("Git", "s2"),
    skill("Python", "s3"),
    bullet("Built responsive interfaces using React and Tailwind CSS", "b1"),
    bullet("Created REST APIs using Flask", "b2"),
    bullet("Deployed the app to the cloud", "b3"),
]


# ── the four states ──────────────────────────────────────────────────────────

def test_explicit_when_the_requirement_is_named():
    assert evaluate_requirement("React", RESUME)["state"] == EXPLICIT


def test_explicit_from_the_skills_line_alone():
    """BUG-070: Git was printed on the resume and still came back as a gap."""
    result = evaluate_requirement("Git", RESUME)
    assert result["state"] == EXPLICIT
    assert result["evidence"][0]["bullet_id"] == "s2"


def test_inferred_through_the_relationship_graph():
    """The original complaint: Tailwind work is evidence of CSS and JavaScript."""
    result = evaluate_requirement("JavaScript", RESUME)
    assert result["state"] == INFERRED
    assert result["inferred_from"] == ["react"]
    assert result["evidence"][0]["text"].startswith("Built responsive interfaces")


def test_partial_when_only_the_general_skill_shows_up():
    result = evaluate_requirement("AWS", RESUME)
    assert result["state"] == PARTIAL
    assert result["inferred_from"] == ["cloud"]


def test_none_when_there_is_genuinely_nothing():
    assert evaluate_requirement("Kubernetes", RESUME)["state"] == NONE
    assert evaluate_requirement("Kubernetes", RESUME)["evidence"] == []


def test_every_match_carries_its_evidence():
    for requirement in ("React", "JavaScript", "AWS"):
        result = evaluate_requirement(requirement, RESUME)
        assert result["evidence"], f"{requirement} matched with nothing to show"
        assert all(e["bullet_id"] for e in result["evidence"])


# ── matching precision ───────────────────────────────────────────────────────

def test_matching_is_token_wise_not_substring():
    """'c' must not match 'css', or every C-requirement matches every CSS resume."""
    assert evaluate_requirement("C", [bullet("Wrote CSS and JavaScript")])["state"] == NONE


def test_aliases_still_apply():
    assert evaluate_requirement("Postgres", [skill("PostgreSQL")])["state"] == EXPLICIT


def test_backwards_inference_is_capped_at_partial():
    """Knowing CSS is weak evidence you could pick up Tailwind — and no more than that.

    The safety property isn't that the reverse direction is ignored, it's that it can never
    reach INFERRED, never lands in `matched`, and so can never back a proposed edit."""
    result = evaluate_requirement("Tailwind", [skill("CSS")])
    assert result["state"] == PARTIAL
    assert evaluate_requirements(["Tailwind"], [skill("CSS")])["matched"] == []


# ── scoring ──────────────────────────────────────────────────────────────────

def test_score_is_deterministic_from_the_states():
    result = evaluate_requirements(["React", "Kubernetes"], RESUME)
    # (1.0 + 0.0) / 2 capability, (1 + 0) / 2 keyword → 0.7*50 + 0.3*50
    assert result["capability_score"] == 50.0
    assert result["keyword_score"] == 50.0
    assert result["score"] == 50.0


def test_capability_and_keyword_scores_diverge_on_inferred_evidence():
    """The point of two numbers: he can do it, but an ATS will not see it."""
    result = evaluate_requirements(["JavaScript"], RESUME)
    assert result["capability_score"] == 80.0
    assert result["keyword_score"] == 0.0


def test_importance_weights_the_score():
    required_only = evaluate_requirements(
        [{"skill": "React", "importance": "required"}, {"skill": "Kubernetes", "importance": "nice_to_have"}],
        RESUME,
    )
    flipped = evaluate_requirements(
        [{"skill": "React", "importance": "nice_to_have"}, {"skill": "Kubernetes", "importance": "required"}],
        RESUME,
    )
    assert required_only["capability_score"] > flipped["capability_score"]


def test_unknown_importance_falls_back_to_required():
    result = evaluate_requirements([{"skill": "React", "importance": "critical"}], RESUME)
    assert result["requirements"][0]["importance"] == "required"


def test_plain_strings_and_objects_both_work():
    assert evaluate_requirements(["React"], RESUME)["score"] == \
           evaluate_requirements([{"skill": "React"}], RESUME)["score"]


def test_partial_is_not_counted_as_matched():
    """PARTIAL is a question for the user, not a claim the agent may make."""
    result = evaluate_requirements(["AWS"], RESUME)
    assert result["matched"] == [] and result["missing"] == []
    assert result["capability_score"] == 40.0


def test_no_requirements_returns_nothing():
    assert evaluate_requirements([], RESUME) is None
    assert evaluate_requirements(["  "], RESUME) is None


def test_a_resume_with_no_evidence_scores_zero():
    result = evaluate_requirements(["React", "CSS"], [])
    assert result["score"] == 0.0
    assert result["missing"] == ["React", "CSS"]


def test_the_original_complaint_end_to_end():
    """Shawn's resume against a JD asking for CSS, JavaScript and Git: no false gaps."""
    result = evaluate_requirements(["CSS", "JavaScript", "Git"], RESUME)
    assert result["missing"] == []
    assert [r["state"] for r in result["requirements"]] == [EXPLICIT, INFERRED, EXPLICIT]


# ── wiring: what a job actually scores against ───────────────────────────────

def test_job_scoring_reads_structured_evidence(client, _db, insert_job):
    """The BUG-070 case as the app runs it: a JD wanting CSS, JavaScript and Git against a
    resume whose evidence is Tailwind, React and a Git skill entry."""
    from services.openai_services import ResumeEntryExtraction, ResumeStructure
    from services.resume_evidence import save_resume_evidence
    from services.skill_evidence import recompute_user_matches

    client.post("/api/auth/signup", json={"username": "wireduser", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "wireduser", "password": "pw123456"})
    user_id = client.get("/api/auth/me").get_json()["id"]

    with _db.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (user_id, raw_description, title, skills) VALUES (%s, %s, 'Frontend', %s) RETURNING id",
            (user_id, "x" * 60, '["CSS", "JavaScript", "Git", "Kubernetes"]'),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(
            entries=[ResumeEntryExtraction(
                kind="project", title="Calendar Map",
                bullets=["Built responsive interfaces using React and Tailwind CSS"],
            )],
            skills=["Git", "Python"],
        ))
        recompute_user_matches(cur, user_id)
        cur.execute("SELECT match_score, match_detail FROM jobs WHERE id = %s", (job_id,))
        score, detail = cur.fetchone()
    _db.commit()

    states = {r["requirement"]: r["state"] for r in detail["requirements"]}
    assert states == {
        "CSS": EXPLICIT,            # the bullet literally says Tailwind CSS
        "JavaScript": INFERRED,     # via React
        "Git": EXPLICIT,            # from the skills line, which is evidence now
        "Kubernetes": NONE,         # genuinely absent
    }
    assert detail["missing"] == ["Kubernetes"]
    assert float(score) > 0


def test_jobs_saved_before_importance_existed_still_score(_db, insert_job):
    """`requirements` is empty on older rows; they fall back to the flat skills list."""
    from services.skill_evidence import requirements_for_job

    assert requirements_for_job([], ["React"]) == ["React"]
    assert requirements_for_job([{"skill": "React", "importance": "preferred"}], ["React"]) == \
           [{"skill": "React", "importance": "preferred"}]


# ── generic competencies (the second round of false gaps) ────────────────────

SHAWN = [
    skill("Python", "s1"),
    skill("Java", "s2"),
    skill("Git", "s3"),
    bullet("Built responsive interfaces using React and Tailwind CSS", "b1"),
    bullet("Implemented a 4-state machine validated with 15+ Google Test unit tests", "b2"),
    bullet("Designed a FastAPI backend with Supabase", "b3"),
]


def test_software_testing_is_inferred_from_a_test_framework():
    """'No evidence found for software testing experience' — from a resume with Google Test."""
    result = evaluate_requirement("software testing", SHAWN)
    assert result["state"] == INFERRED
    assert result["inferred_from"] == ["google test"]


def test_backend_is_inferred_from_a_backend_framework():
    """The word itself never appears — only the framework does."""
    result = evaluate_requirement("backend", [bullet("Created REST APIs using Flask", "b1")])
    assert result["state"] == INFERRED
    assert result["inferred_from"] == ["flask"]


def test_programming_is_inferred_from_any_named_language():
    result = evaluate_requirement("programming", SHAWN)
    assert result["state"] == INFERRED


def test_generic_phrasings_normalize_before_matching():
    assert evaluate_requirement("Relational Databases", [skill("PostgreSQL")])["state"] == INFERRED
    assert evaluate_requirement("Continuous Integration", [skill("GitHub Actions")])["state"] == INFERRED
    assert evaluate_requirement("Cloud Platforms", [skill("AWS")])["state"] == INFERRED


def test_vague_requirements_are_dropped_before_they_reach_matching():
    """Nothing can be evidence of 'debugging', so it must never become a requirement."""
    from services.openai_services import VAGUE_REQUIREMENTS

    for term in ("programming", "debugging", "documentation", "problem solving"):
        assert term in VAGUE_REQUIREMENTS


def test_underscored_vague_terms_are_caught(monkeypatch):
    """The model emits software_development as readily as software development."""
    from services.match import normalize_skill
    from services.openai_services import VAGUE_REQUIREMENTS

    assert normalize_skill("software_development") in VAGUE_REQUIREMENTS
    assert normalize_skill("Problem_Solving") in VAGUE_REQUIREMENTS


def test_ai_is_inferred_from_machine_learning_work():
    """A JD asking for AI, against a resume describing gesture recognition."""
    result = evaluate_requirement("AI", [bullet("Developed a gesture recognition system with FEAGI")])
    assert result["state"] == INFERRED
    assert result["inferred_from"] == ["gesture recognition"]


def test_artificial_intelligence_normalizes_to_ai():
    result = evaluate_requirement("Artificial Intelligence", [skill("Machine Learning")])
    assert result["state"] == INFERRED
