"""Evidence-based requirement matching: states, evidence, and the score built from them."""

import pytest

from services.skill_evidence import (
    EXPLICIT,
    STATE_WEIGHTS,
    INFERRED,
    NONE,
    PARTIAL,
    as_condition,
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
    # React EXPLICIT (1.0), Kubernetes NONE (0.0) → capability 50%
    assert result["fit_score"] == 50.0
    # and of the one requirement there IS evidence for, it is stated outright
    assert result["visibility_score"] == 100.0
    # the headline is capability, unblended
    assert result["score"] == 50.0


def test_capability_and_communication_measure_different_things():
    """The point of two numbers: he can do it, but the resume does not say so.

    Communication is conditional on capability — it asks "of what you can demonstrably do,
    how much is stated plainly", so a gap cannot drag it down and a keyword cannot lift
    capability.
    """
    inferred = evaluate_requirements(["JavaScript"], RESUME)      # React implies it
    assert inferred["fit_score"] == 80.0
    assert inferred["visibility_score"] == 0.0                 # nothing says "JavaScript"
    assert inferred["hidden"] == ["JavaScript"]                   # exactly what tailoring is for

    # a requirement with no evidence at all changes capability, never communication
    with_gap = evaluate_requirements(["JavaScript", "Kubernetes"], RESUME)
    assert with_gap["fit_score"] == 40.0                   # halved by the gap
    assert with_gap["visibility_score"] == 0.0                 # unchanged


def test_communication_is_none_when_there_is_nothing_to_communicate():
    result = evaluate_requirements(["Kubernetes"], RESUME)
    assert result["fit_score"] == 0.0
    assert result["visibility_score"] is None
    assert result["hidden"] == []


def test_wording_moves_communication_and_capability_stays_put():
    """The incentive the split exists to remove: naming the skill outright must not look
    like the candidate became more capable."""
    inferred = evaluate_requirements(["JavaScript"], RESUME)
    explicit = evaluate_requirements(["JavaScript"], RESUME + [skill("JavaScript")])

    assert explicit["visibility_score"] > inferred["visibility_score"]
    assert explicit["fit_score"] >= inferred["fit_score"]
    assert explicit["hidden"] == []


def test_importance_weights_the_score():
    required_only = evaluate_requirements(
        [{"skill": "React", "importance": "required"}, {"skill": "Kubernetes", "importance": "nice_to_have"}],
        RESUME,
    )
    flipped = evaluate_requirements(
        [{"skill": "React", "importance": "nice_to_have"}, {"skill": "Kubernetes", "importance": "required"}],
        RESUME,
    )
    assert required_only["fit_score"] > flipped["fit_score"]


def test_unknown_importance_falls_back_to_required():
    result = evaluate_requirements([{"skill": "React", "importance": "critical"}], RESUME)
    assert result["requirements"][0]["importance"] == "required"


def test_plain_strings_and_objects_both_work():
    assert evaluate_requirements(["React"], RESUME)["score"] == \
           evaluate_requirements([{"skill": "React"}], RESUME)["score"]


def test_related_experience_is_not_counted_as_matched_and_scores_nothing():
    """PARTIAL is something to tell the user about, not capability.

    It used to carry 0.4, so a resume showing "cloud" scored 40% against an AWS requirement it
    never demonstrated. `matched` always excluded it; the score did not, and the number is
    what people read."""
    result = evaluate_requirements(["AWS"], RESUME)
    assert result["matched"] == [] and result["missing"] == []
    assert result["fit_score"] == 0.0


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


# ── the entry header is evidence too ────────────────────────────────────────

def test_a_technology_named_only_in_the_entry_header_counts(_db):
    """A project header saying "(C++, Google Test)" names skills no bullet repeats. The
    resume does state them, so scoring must see them — but with no bullet id, because no
    single bullet demonstrates them."""
    from services.openai_services import ResumeEntryExtraction, ResumeStructure
    from services.resume_evidence import save_resume_evidence
    from services.skill_evidence import load_evidence_bullets

    with _db.cursor() as cur:
        cur.execute("TRUNCATE resume_bullets, resume_entries, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('headeruser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(
                kind="project",
                title="Motor Control System (C++, Google Test)",
                bullets=["Implemented fault handling for the drive controller"],
            )
        ]))
        evidence = load_evidence_bullets(cur, user_id)

    header = [e for e in evidence if e.get("source") == "entry_header"]
    assert header and "Google Test" in header[0]["text"]
    assert header[0]["bullet_id"] is None          # states it; does not demonstrate it

    assert evaluate_requirement("Google Test", evidence)["state"] == EXPLICIT
    assert evaluate_requirement("Terraform", evidence)["state"] == NONE


# ── requirement conditions: "one of Java, Python, C++" is ONE requirement ────

def any_of(*items, minimum=1, importance="required", source_text=None):
    return {"importance": importance, "type": "skill", "source_text": source_text,
            "condition": {"operator": "any_of", "minimum": minimum, "items": list(items)}}


def all_of(*items, importance="required"):
    return {"importance": importance, "type": "skill", "source_text": None,
            "condition": {"operator": "all_of", "items": list(items)}}


CPP = [{"bullet_id": "b1", "kind": "project",
        "text": "Built a motor controller in C++ with Google Test"}]


def test_one_alternative_satisfies_the_whole_requirement():
    """The bug this exists for: flattened, six alternatives scored 1/6 = 17%."""
    grouped = evaluate_requirements([any_of("Java", "Python", "JavaScript", "HTML", "SQL", "C++")], CPP)
    flattened = evaluate_requirements([{"skill": s} for s in
                                       ["Java", "Python", "JavaScript", "HTML", "SQL", "C++"]], CPP)

    assert grouped["fit_score"] == 100.0
    assert flattened["fit_score"] < 20.0        # the old, wrong denominator


def test_unmet_alternatives_are_not_gaps():
    """Being asked for "Java or Python" and knowing Python is not a Java gap — and Java must
    never reach tailoring as something to close."""
    result = evaluate_requirements([any_of("Java", "C++")], CPP)

    assert result["missing"] == []
    assert result["hidden"] == []
    assert result["requirements"][0]["satisfied_by"] == ["C++"]


def test_all_of_is_only_as_strong_as_its_weakest_item():
    result = evaluate_requirements([all_of("C++", "Terraform")], CPP)

    assert result["requirements"][0]["state"] == NONE
    assert result["missing"] == ["C++ and Terraform"]


def test_any_of_with_a_minimum_needs_that_many():
    one = evaluate_requirements([any_of("Java", "Python", "C++", minimum=1)], CPP)
    two = evaluate_requirements([any_of("Java", "Python", "C++", minimum=2)], CPP)

    assert one["requirements"][0]["state"] == EXPLICIT
    assert two["requirements"][0]["state"] == NONE     # only C++ is there


def test_a_group_carries_one_weight_however_many_alternatives():
    """Six alternatives must not outvote a single-skill requirement."""
    result = evaluate_requirements(
        [any_of("Java", "Python", "JavaScript", "HTML", "SQL", "C++"), {"skill": "Terraform"}],
        CPP,
    )
    assert result["fit_score"] == 50.0          # one satisfied, one not


def test_the_postings_own_words_are_the_label_when_we_have_them():
    listed = evaluate_requirements([any_of("Java", "C++")], CPP)["requirements"][0]
    quoted = evaluate_requirements(
        [any_of("Java", "C++", source_text="experience in one of Java or C++")], CPP
    )["requirements"][0]

    assert listed["requirement"] == "Java or C++"
    assert quoted["requirement"] == "experience in one of Java or C++"


def test_legacy_and_tree_shapes_run_through_the_same_resolver():
    assert as_condition({"skill": "C++"}) == {"operator": "any_of", "minimum": 1, "items": ["C++"]}
    assert as_condition("C++")["items"] == ["C++"]
    assert evaluate_requirements([{"skill": "C++"}], CPP)["fit_score"] == \
           evaluate_requirements([any_of("C++")], CPP)["fit_score"]


def test_a_satisfied_group_can_still_be_hidden():
    """An inferred alternative satisfies the requirement but is not stated — which is
    exactly the case tailoring exists for."""
    react = [{"bullet_id": "b1", "kind": "project", "text": "Built the frontend in React"}]
    result = evaluate_requirements([any_of("JavaScript", "Ruby")], react)

    assert result["requirements"][0]["state"] == INFERRED
    assert result["hidden"] == ["JavaScript or Ruby"]
    assert result["visibility_score"] == 0.0


# ── the run of 2026-09-21: three requirements matched through nothing ────────
# Kafka via "messaging", Redis via "database", AWS via "cloud" — and the agent then asked how
# Redis had improved a project whose bullet names PostgreSQL. These are the exact bullets.

MESSAGING = bullet(
    "Built an end-to-end encrypted messaging platform with React and Flask, keeping "
    "cryptographic operations in the browser so the backend stores only ciphertext.",
    "b-msg",
)
DEPLOY = bullet(
    "Deployed separate Dockerized web/worker services on Railway with GitHub Actions CI, "
    "Sentry, and 772 automated tests (668 backend / 104 frontend), including backend tests "
    "against a throwaway PostgreSQL database built from schema.",
    "b-deploy",
)
LIDAR = bullet(
    "Working on Velodyne VLP-16 point-cloud filtering, ground removal, Euclidean clustering, "
    "cone validation/centroid estimation, and LiDAR-to-camera projection with camera-based "
    "cone-color classification.",
    "b-lidar",
)
BAD_RUN = [MESSAGING, DEPLOY, LIDAR]


def test_a_postgresql_bullet_is_not_evidence_of_redis():
    """Related experience: the resume shows database work, the posting wants Redis. Still
    reported, so the user can act on it — but worth zero, and never a question."""
    result = evaluate_requirement("Redis", BAD_RUN)

    assert result["state"] == PARTIAL
    assert result["inferred_from"] == ["database"]
    assert STATE_WEIGHTS[result["state"]] == 0.0


def test_a_lidar_bullet_is_not_evidence_of_aws():
    """"point-cloud" is not cloud computing. This one is not even related experience: once the
    compound stops leaking its head noun there is no relation left to report."""
    result = evaluate_requirement("AWS", BAD_RUN)

    assert result["state"] == NONE
    assert result["evidence"] == []


def test_an_encrypted_chat_bullet_is_not_evidence_of_kafka():
    """"messaging" means a chat app here and a message broker in the posting. Whatever the
    learned relation says, the reverse direction cannot make this a match worth anything."""
    result = evaluate_requirement("Kafka", BAD_RUN)

    assert result["state"] in (NONE, PARTIAL)
    assert STATE_WEIGHTS[result["state"]] == 0.0


def test_the_whole_bad_run_scores_zero_against_those_three():
    result = evaluate_requirements(["Kafka", "Redis", "AWS"], BAD_RUN)

    assert result["matched"] == []
    assert result["fit_score"] == 0.0


# ── the fixes must not become a different bug ────────────────────────────────

def test_valid_matches_still_work():
    """Every one of these was working before the compound and PARTIAL changes, and each is a
    way the fix could have gone too far."""
    # says Tailwind and never says CSS, or this would be EXPLICIT and prove nothing
    tailwind = bullet("Built responsive interfaces with Tailwind and shadcn", "b-tw")
    assert evaluate_requirement("CSS", [tailwind])["state"] == INFERRED

    redis = bullet("Cached sessions in Redis to cut login latency", "b-redis")
    assert evaluate_requirement("Redis", [redis])["state"] == EXPLICIT

    java = bullet("Server-side rendering with a Java-based service", "b-java")
    assert evaluate_requirement("Java", [java])["state"] == EXPLICIT

    ml = bullet("Built a machine-learning pipeline for ranking", "b-ml")
    assert evaluate_requirement("machine learning", [ml])["state"] == EXPLICIT


def test_a_protected_compound_suppresses_only_its_own_occurrence():
    """The case a word blocklist would fail. "point clouds" must not match cloud; the "cloud
    infrastructure" later in the same sentence must."""
    both = bullet("Processed point clouds and deployed to cloud infrastructure", "b-both")
    assert evaluate_requirement("cloud", [both])["state"] == EXPLICIT

    only_points = bullet("Processed point clouds from the LiDAR rig", "b-points")
    assert evaluate_requirement("cloud", [only_points])["state"] == NONE
