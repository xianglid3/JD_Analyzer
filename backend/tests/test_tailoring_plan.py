from services.tailoring_plan import (
    keyword_only,
    agent_candidates,
    build_tailoring_plan,
    deterministic_gaps,
    rewrite_candidates,
)


def requirement(name, state, *, source=None, citable=True, evidence_text="evidence"):
    return {
        "requirement": name,
        "state": state,
        "importance": "required",
        "inferred_from": [source] if source else [],
        "satisfied_by": [],
        "evidence": ([{"bullet_id": "bullet-1", "text": evidence_text}] if citable else
                     [{"bullet_id": None, "text": "header"}]),
    }


def test_every_requirement_gets_one_bounded_outcome():
    assessment = {"requirements": [
        requirement("Kubernetes", "EXPLICIT"),
        requirement("CSS", "INFERRED", source="tailwind"),
        requirement("JavaScript", "INFERRED", source="typescript"),
        requirement("Object-oriented programming", "INFERRED", source="cpp"),
        requirement("AWS", "PARTIAL", source="cloud"),
        requirement("Terraform", "NONE", citable=False),
        requirement("Python", "EXPLICIT", citable=False),
    ]}

    plan = build_tailoring_plan(assessment)

    assert [item["action"] for item in plan] == [
        "rewrite", "surface_skill", "confirm", "confirm", "confirm", "gap", "only_in_skills",
    ]
    assert len(plan) == len(assessment["requirements"])
    assert [item["requirement"] for item in deterministic_gaps(plan)] == ["Terraform"]
    assert [item["requirement"] for item in rewrite_candidates(plan)] == ["Kubernetes"]
    # matched, but by a keyword rather than by work — reported instead of silently kept
    assert [item["requirement"] for item in keyword_only(plan)] == ["Python"]


def test_empty_assessment_produces_no_agent_work_or_fake_gaps():
    assert build_tailoring_plan(None) == []
    assert rewrite_candidates([]) == []
    assert agent_candidates([]) == []
    assert deterministic_gaps([]) == []


def test_already_strong_unmeasured_bullet_asks_for_detail_not_cosmetic_rewriting():
    assessment = {"requirements": [
        requirement(
            "FastAPI",
            "EXPLICIT",
            evidence_text=(
                "Designed a FastAPI backend with Supabase to handle event creation, updates, "
                "and geocoded location storage through RESTful APIs"
            ),
        ),
    ]}

    plan = build_tailoring_plan(assessment)

    assert plan[0]["action"] == "strengthen"
    assert rewrite_candidates(plan) == []
    assert agent_candidates(plan) == [plan[0]]
    assert "impact or scale" in plan[0]["reason"]


def test_strong_measured_bullet_needs_no_agent_work():
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "FastAPI", "EXPLICIT",
            evidence_text="Built FastAPI services that processed 10,000 requests per day reliably",
        ),
    ]})

    assert plan[0]["action"] == "keep"
    assert agent_candidates(plan) == []


def test_partial_citable_evidence_becomes_a_confirmation_candidate():
    plan = build_tailoring_plan({"requirements": [
        requirement("Redux", "PARTIAL", evidence_text="Built a React dashboard"),
    ]})

    assert plan[0]["action"] == "confirm"
    assert plan[0]["targets"][0]["text"] == "Built a React dashboard"
    assert agent_candidates(plan) == [plan[0]]


def test_inferred_evidence_without_write_permission_asks_for_confirmation():
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "JavaScript", "INFERRED", source="typescript",
            evidence_text="Built the frontend with React and TypeScript",
        ),
    ]})

    assert plan[0]["action"] == "confirm"
    assert plan[0]["targets"][0]["text"] == "Built the frontend with React and TypeScript"
    assert agent_candidates(plan) == [plan[0]]


def test_only_one_strong_bullet_per_run_becomes_a_detail_candidate():
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "React", "EXPLICIT",
            evidence_text="Built a React application for daily nutrition tracking and meal planning",
        ),
        requirement(
            "TypeScript", "EXPLICIT",
            evidence_text="Developed a TypeScript calendar interface for customers across multiple locations",
        ),
    ]})

    assert [item["action"] for item in plan] == ["strengthen", "keep"]
    assert len(agent_candidates(plan)) == 1


def test_rewrite_candidate_carries_target_text_and_specific_weakness_but_no_id():
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "Kubernetes", "EXPLICIT",
            evidence_text="Worked on Kubernetes deployments",
        ),
    ]})

    target = plan[0]["targets"][0]
    assert target["text"] == "Worked on Kubernetes deployments"
    assert "action verb" in target["weakness"]
    assert "bullet-1" not in str(target)


# ── a candidate has to be addressable ────────────────────────────────────────
# A grouped requirement is labelled with the posting's own sentence, which is right on screen
# and unusable as a handle: the agent must echo it back exactly to say what it is working on.
# One real run spent its last four steps being told "requirement is not an approved tailoring
# candidate" while trying to name a hundred-character sentence — and that sentence quoted six
# skills for a group of four, so it was wrong as a description too.

GROUPED = {"requirements": [{
    "requirement": (
        "Applied knowledge in object-oriented design, Java, Python, C, C++ and "
        "multi-threaded programming is desirable."
    ),
    "agent_label": "java or python or c or cpp",
    "state": "EXPLICIT",
    "importance": "required",
    "evidence": [{"bullet_id": "b1", "text": "Wrote a parser"}],
}]}


def test_the_agent_label_is_short_even_when_the_display_name_is_a_sentence():
    item = build_tailoring_plan(GROUPED)[0]
    assert item["agent_label"] == "java or python or c or cpp"
    assert item["requirement"].startswith("Applied knowledge")   # the user still reads this


def test_a_plain_requirement_needs_no_separate_handle():
    plan = build_tailoring_plan({"requirements": [
        requirement("Kubernetes", "EXPLICIT"),
    ]})
    assert plan[0]["agent_label"] == plan[0]["requirement"]
