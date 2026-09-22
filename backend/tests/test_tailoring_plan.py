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
        # AWS-via-cloud is a gap now, not a confirmation: showing the general skill is not
        # evidence of the specific tool, and asking about it presumes it was used
        "rewrite", "surface_skill", "inferred_only", "inferred_only", "gap", "gap", "only_in_skills",
    ]
    assert len(plan) == len(assessment["requirements"])
    assert [item["requirement"] for item in deterministic_gaps(plan)] == ["AWS", "Terraform"]
    assert [item["requirement"] for item in rewrite_candidates(plan)] == ["Kubernetes"]
    # matched, but by a keyword rather than by work — reported instead of silently kept
    assert [item["requirement"] for item in keyword_only(plan)] == ["Python"]


def test_empty_assessment_produces_no_agent_work_or_fake_gaps():
    assert build_tailoring_plan(None) == []
    assert rewrite_candidates([]) == []
    assert agent_candidates([]) == []
    assert deterministic_gaps([]) == []


def test_a_strong_bullet_without_a_number_is_kept():
    """It used to become a `strengthen` task, and the model filled it with "what improvements
    did X bring?" about a bullet that already said. A missing number is not a missing fact."""
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

    assert plan[0]["action"] == "keep"
    assert agent_candidates(plan) == []


def test_strong_measured_bullet_needs_no_agent_work():
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "FastAPI", "EXPLICIT",
            evidence_text="Built FastAPI services that processed 10,000 requests per day reliably",
        ),
    ]})

    assert plan[0]["action"] == "keep"
    assert agent_candidates(plan) == []


def test_related_experience_becomes_a_gap_not_a_question():
    """A live run asked "How did Redis improve this project?" about a bullet naming only
    PostgreSQL. The premise came from here: general evidence became a `confirm` candidate,
    and confirming presumes there is something to confirm.

    It is a gap now, with no target attached — citing the PostgreSQL bullet under a Redis
    heading is the other half of what made that run look broken."""
    plan = build_tailoring_plan({"requirements": [
        requirement("Redux", "PARTIAL", evidence_text="Built a React dashboard"),
    ]})

    assert plan[0]["action"] == "gap"
    assert plan[0]["targets"] == []
    assert agent_candidates(plan) == []
    assert "nothing names Redux" in plan[0]["reason"]


def test_inferred_evidence_is_not_a_question():
    """It used to be `confirm`, which turned an inference into "did you use JavaScript?" — the
    run that asked a React bullet about front-end frameworks and a messaging bullet about data
    structures. A match alone creates no task; the user claims the skill themselves."""
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "JavaScript", "INFERRED", source="typescript",
            evidence_text="Built the frontend with React and TypeScript",
        ),
    ]})

    assert plan[0]["action"] == "inferred_only"
    assert plan[0]["targets"] == []
    assert agent_candidates(plan) == []
    assert "typescript" in plan[0]["reason"] and "I used this" in plan[0]["reason"]


def test_the_idempotency_bullet_is_kept():
    """The post-patch run asked "what improvements did the idempotency implementation bring?"
    The bullet states its outcome; the outcome regex just does not recognise the wording."""
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "LLM", "EXPLICIT",
            evidence_text=(
                "Implemented reserve-before-spend idempotency for job-draft creation so "
                "duplicate requests cannot trigger duplicate LLM calls, with stored-response "
                "replay after completion."
            ),
        ),
    ]})

    assert [item["action"] for item in plan] == ["keep"]
    assert agent_candidates(plan) == []


def test_a_missing_result_is_never_a_reason_to_ask():
    """Impact and scale doubts still describe the bullet; only ownership and mechanism may
    suggest a question, because only they name a specific missing fact."""
    from services.tailoring_plan import _weakness

    assert "do not ask" in _weakness("Built a Flask API for order tracking", [])
    assert "if you need to ask" in _weakness("Helped with the checkout redesign", [])


def test_rewrite_candidate_carries_its_target_text_weakness_and_id():
    """The id is the planner's own record of which bullet it chose, and what the run later
    hands to the model. Whether the model is told is `job_brief`'s decision, not this one —
    the plan itself has always known, it just used to throw the id away and match on text."""
    plan = build_tailoring_plan({"requirements": [
        requirement(
            "Kubernetes", "EXPLICIT",
            evidence_text="Worked on Kubernetes deployments",
        ),
    ]})

    target = plan[0]["targets"][0]
    assert target["text"] == "Worked on Kubernetes deployments"
    assert "action verb" in target["weakness"]
    assert target["bullet_id"] == "bullet-1"


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


def test_one_bullet_is_not_fought_over_by_two_candidates():
    """A posting lists "java or golang or python or c++…" and "c# or c++ or java" as separate
    requirements, and the same C++ bullet is the evidence for both. The agent was sent at it
    twice, spending a step each time re-deciding work it had already done."""
    shared = "Contributing to a ROS 2 stack in C++."
    assessment = {"requirements": [
        requirement("java or golang or python or c++", "EXPLICIT", evidence_text=shared),
        requirement("c# or c++ or java", "EXPLICIT", evidence_text=shared),
    ]}

    plan = build_tailoring_plan(assessment)
    with_targets = [item for item in plan if item["targets"]]

    assert len(with_targets) == 1, "the same bullet was offered to two candidates"


def test_two_entries_with_identical_wording_each_keep_their_candidate():
    """The de-duplication used to key on bullet text, so a bullet worded the same way under
    two different entries looked like the same bullet and the second candidate lost its
    target. Keying on the id tells them apart."""
    same = "Built REST APIs in Python."
    assessment = {"requirements": [
        {"requirement": "python", "agent_label": "python", "state": "EXPLICIT",
         "importance": "required", "inferred_from": [], "satisfied_by": [],
         "evidence": [{"bullet_id": "bullet-a", "text": same}]},
        {"requirement": "rest", "agent_label": "rest", "state": "EXPLICIT",
         "importance": "required", "inferred_from": [], "satisfied_by": [],
         "evidence": [{"bullet_id": "bullet-b", "text": same}]},
    ]}

    plan = build_tailoring_plan(assessment)

    assert [item["targets"][0]["bullet_id"] for item in plan if item["targets"]] == [
        "bullet-a", "bullet-b",
    ]


def _shared_bullet(order):
    bullet = {"bullet_id": "shared", "text": "Worked on the React dashboard backend"}
    items = {
        "redux": {"requirement": "redux", "agent_label": "redux", "state": "INFERRED",
                  "importance": "required", "inferred_from": ["react"], "evidence": [bullet]},
        "backend": {"requirement": "backend", "agent_label": "backend", "state": "EXPLICIT",
                    "importance": "required", "evidence": [bullet]},
    }
    return build_tailoring_plan({"requirements": [items[name] for name in order]})


def test_requirement_order_does_not_decide_who_owns_a_bullet():
    """Reproduced in review: this bullet became a Redux confirmation when Redux was listed
    first and a backend rewrite when backend was. Confirmations are gone, and ownership among
    what is left is by rank, never by position."""
    def owners(plan):
        return {item["agent_label"]: (item["action"], [t["bullet_id"] for t in item["targets"]])
                for item in plan}

    first, second = _shared_bullet(["redux", "backend"]), _shared_bullet(["backend", "redux"])

    assert owners(first) == owners(second)
    assert owners(first)["redux"] == ("inferred_only", [])
    assert owners(first)["backend"] == ("rewrite", ["shared"])


