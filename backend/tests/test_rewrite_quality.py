from services.claim_check import (
    bullet_is_already_strong,
    bullet_quality_gaps,
    merge_quality_issue,
    rewrite_quality_issue,
)


FASTAPI_ORIGINAL = (
    "Designed a FastAPI backend with Supabase to handle event creation, updates, and "
    "geocoded location storage through RESTful APIs."
)
FASTAPI_PROPOSED = (
    "Designed a FastAPI backend with Supabase to manage event creation, updates, and "
    "geocoded location storage via RESTful APIs."
)
REACT_ORIGINAL = (
    "Developed the frontend using React + TypeScript with FullCalendar, Shadcn UI, and "
    "Mapbox, featuring real-time synchronization between calendar and map views for "
    "seamless user interaction."
)
REACT_PROPOSED = (
    "Developed the frontend using React and TypeScript, incorporating FullCalendar, "
    "Shadcn UI, and Mapbox for real-time synchronization between calendar and map views."
)


def test_polished_technical_bullet_is_recognized_as_already_strong():
    assert bullet_is_already_strong(FASTAPI_ORIGINAL) is True


def test_synonym_swap_is_rejected_as_cosmetic():
    assert "only changes phrasing" in rewrite_quality_issue(FASTAPI_ORIGINAL, FASTAPI_PROPOSED)


def test_rewrite_that_drops_a_useful_outcome_is_rejected():
    assert rewrite_quality_issue(REACT_ORIGINAL, REACT_PROPOSED) is not None


def test_weak_short_bullet_can_still_be_improved():
    assert rewrite_quality_issue(
        "Worked on Kubernetes deployments",
        "Deployed and operated Kubernetes services",
    ) is None


def test_weak_bullet_does_not_bypass_the_cosmetic_edit_check():
    issue = rewrite_quality_issue(
        "Supported API development through Flask",
        "Supported API development via Flask",
    )
    assert "only changes phrasing" in issue


def test_quality_gaps_explain_why_a_target_was_selected():
    assert bullet_quality_gaps("Worked on Kubernetes deployments") == [
        "it does not open with a concrete action verb",
        "it gives very little context or outcome detail",
    ]


def test_strong_bullet_may_surface_new_supported_signal():
    original = "Developed responsive production interfaces with Tailwind for several customer workflows"
    proposed = (
        "Developed responsive production interfaces with Tailwind CSS for several customer workflows"
    )
    assert rewrite_quality_issue(original, proposed) is None


def test_identical_edit_is_rejected():
    assert "same as" in rewrite_quality_issue("Built a Flask API", "Built a Flask API")


def test_grounded_merge_must_compress_repetition_without_dropping_signals():
    originals = [
        "Built Flask APIs for calendar events",
        "Built Flask endpoints for event updates",
    ]
    assert merge_quality_issue(
        originals,
        "Built Flask APIs for calendar event creation and updates",
    ) is None
    assert "concatenates" in merge_quality_issue(
        originals,
        "Built Flask APIs for calendar events and built Flask endpoints for event updates",
    )


def test_merge_cannot_drop_a_metric():
    issue = merge_quality_issue(
        ["Processed 2M events with Python", "Built Python ingestion jobs"],
        "Built Python ingestion jobs for events",
    )
    assert "measurable result" in issue


# ── adding a skill to a strong bullet ────────────────────────────────────────
# `show_in_bullet` deliberately targets bullets that are ALREADY strong — they were chosen
# because the user said the skill belongs to that entry, not because the bullet was weak. The
# quality gate was tuned for weak bullets, where a rewrite means restructuring, so every
# attempt to work a skill into a dense 27-word bullet was refused for dropping detail. Seven
# candidates, twelve steps, zero edits.

DENSE = (
    "Implemented a 4-state machine (READY, RUNNING, WARNING, FAULT) with fault detection "
    "for overcurrent, undervoltage, and stall conditions; safe shutdown <10ms, "
    "validated with 15+ Google Test unit tests."
)


def test_working_a_skill_into_a_dense_bullet_is_allowed():
    added = (
        "Implemented a 4-state machine (READY, RUNNING, WARNING, FAULT) in C++ with fault "
        "detection for overcurrent, undervoltage, and stall conditions; safe shutdown "
        "<10ms, validated with 15+ Google Test unit tests."
    )
    assert rewrite_quality_issue(DENSE, added) is None


def test_the_refusal_says_what_to_do_not_only_what_was_wrong():
    """The model was told "removes too much of the bullet's existing detail" three times and
    kept shortening, because nothing in that sentence says the job is additive."""
    issue = rewrite_quality_issue(DENSE, "Implemented a state machine with data structures.")
    assert "add to it" in issue
    # …and it no longer says "a rewrite should not be shorter than what it replaces", because
    # that stopped being true: shortening is allowed, dropping evidence to do it is not.
    assert "not by dropping" in issue


def test_dropping_a_number_names_the_number_that_was_dropped():
    issue = rewrite_quality_issue(
        DENSE,
        "Implemented a 4-state machine (READY, RUNNING, WARNING, FAULT) with fault detection "
        "for overcurrent, undervoltage, and stall conditions; validated with Google Test "
        "unit tests across the whole controller.",
    )
    assert "10ms" in issue or "15" in issue
    assert "keep every one of them" in issue


SLASH_ORIGINAL = (
    "Designed a FastAPI backend with Supabase and REST APIs for event creation, updates, and "
    "geocoded location storage; integrated it with a React/TypeScript calendar and Mapbox "
    "interface."
)


def test_slash_joined_skills_are_each_recognized():
    from services.claim_check import named_skills

    found = {skill.lower() for skill in named_skills("a React/TypeScript calendar")}
    assert {"react", "typescript"} <= found


def test_rewrite_that_drops_one_side_of_a_slash_pair_is_refused():
    proposed = (
        "Developed a FastAPI backend with Python and TypeScript, integrating REST APIs for "
        "event creation, updates, and geocoded location storage in Supabase."
    )
    issue = rewrite_quality_issue(SLASH_ORIGINAL, proposed)
    assert issue and "react" in issue
