"""What a rewrite may and may not say.

`test_tailoring_agent.py` covers the tool boundary; this covers the rules underneath it.
"""

from services.claim_check import (
    answer_detail_terms,
    abstraction_padding,
    compression_only,
    explicit_answer_contradictions,
    improvements,
    bullet_is_already_strong,
    bullet_quality_gaps,
    numeric_claims,
    omitted_answer_compounds,
    ownership_inflation,
    recruiter_doubt,
    rewrite_quality_issue,
    rewrite_validation_findings,
    tense_regression,
)


# ── who did the work ─────────────────────────────────────────────────────────

def test_contributing_to_may_not_become_developed():
    """The reported case. Every noun survives, the verb is promoted, and the claim checker sees
    nothing wrong because no technology or number changed — but the two sentences describe
    different people."""
    original = ("Contributing to a work-in-progress ROS 2 autonomous-driving stack in C++ for "
                "Pitt's FSAE EV driverless program, focused on cone-perception software.")
    proposed = ("Developed cone-perception software in C++ for the ROS 2 autonomous-driving "
                "stack in Pitt's FSAE EV driverless program.")

    issue = rewrite_quality_issue(original, proposed)

    assert issue is not None
    assert "contributing" in issue and "developed" in issue


def test_keeping_the_hedge_is_allowed():
    """Rewriting *around* shared credit is the whole point; only promoting it is refused. Tested
    against the rule itself, because these examples are also cosmetic and would be refused for
    that instead — a different complaint with a different fix."""
    assert ownership_inflation(
        "Contributing to a ROS 2 stack in C++, focused on cone perception.",
        "Contributed cone-perception modules in C++ to a ROS 2 autonomous-driving stack.",
    ) is None


def test_a_bullet_that_already_claims_ownership_keeps_it():
    """"Built and contributed to" already says built; the rewrite inflates nothing."""
    assert ownership_inflation(
        "Built and contributed to a Kubernetes deployment pipeline across three regions.",
        "Built a Kubernetes deployment pipeline across three regions, cutting deploy time.",
    ) is None


def test_an_unhedged_bullet_is_not_this_rule_s_business():
    assert ownership_inflation(
        "Worked on Kubernetes deployments across three regions",
        "Deployed Kubernetes services across three regions",
    ) is None


def test_a_framework_version_is_not_a_measurement():
    """"ROS 2 stack" used to parse as two seconds, because the unit alternation matched the "s"
    of "stack" — so any rewrite that kept "ROS 2" was refused for dropping a measurable result.
    "Vue 3 app" and "Python 3 script" are the same shape."""
    assert numeric_claims("Contributing to a ROS 2 stack") == {(2.0, "")}
    assert numeric_claims("Built a Vue 3 app") == {(3.0, "")}
    # and the distinction the function exists for still holds
    assert numeric_claims("Cut latency 30%") == {(30.0, "%")}
    assert numeric_claims("12 s response time") == {(12.0, "s")}


# ── is the work finished? ────────────────────────────────────────────────────

FSAE = ("Contributing to a work-in-progress ROS 2 autonomous-driving stack in C++ for "
        "Pitt's FSAE EV driverless program, focused on cone-perception software.")


def test_ongoing_work_may_not_be_rewritten_as_finished():
    """The reported case, and a different rule from ownership: "Contributing" and "Contributed"
    are both shared credit, so `ownership_inflation` passes them. What changed is whether the
    work is over, which a recruiter reading a current role takes literally."""
    proposed = ("Contributed C++ software to a ROS 2 autonomous-driving stack, focusing on "
                "cone perception and integrating perception components within the robotics "
                "pipeline.")

    assert ownership_inflation(FSAE, proposed) is None
    assert tense_regression(FSAE, proposed, entry_is_ongoing=True) is not None


def test_a_confirmed_skill_does_not_license_the_tense_change():
    """`surfacing` exempts the phrasing check so a confirmed skill can enter the bullet. It
    does not exempt the factual ones. This proposal is clean apart from the tense, so the
    phrasing check would have let it pass on the exemption alone."""
    proposed = ("Contributed cone-perception software in C++ to Pitt's ROS 2 "
                "autonomous-driving stack for the FSAE EV driverless program, applying "
                "robotics throughout.")

    assert rewrite_quality_issue(FSAE, proposed, surfacing="robotics") is None
    issue = rewrite_quality_issue(
        FSAE, proposed, surfacing="robotics", entry_is_ongoing=True,
    )
    assert issue is not None and "still going on" in issue


def test_a_finished_project_may_be_put_in_the_past():
    """The reason this is gated on `end_date` and not on a participle list: this exact rewrite
    is correct when the entry actually ended."""
    assert tense_regression(
        "Implementing a Kubernetes deployment pipeline across three regions.",
        "Implemented a Kubernetes deployment pipeline across three regions.",
        entry_is_ongoing=False,
    ) is None


def test_a_rewrite_that_keeps_the_ongoing_sense_is_fine():
    assert tense_regression(
        FSAE,
        "Contributing cone-perception software in C++ to Pitt's in-progress ROS 2 stack.",
        entry_is_ongoing=True,
    ) is None


def test_a_bullet_that_never_read_as_ongoing_is_not_this_rule_s_business():
    assert tense_regression(
        "Built a Kubernetes deployment pipeline across three regions.",
        "Built and operated a Kubernetes deployment pipeline across three regions.",
        entry_is_ongoing=True,
    ) is None



def test_an_ongoing_bullet_can_be_strengthened_without_changing_tense():
    """The deadlock this pair of checks used to make: on a current role the past-tense rewrite
    was refused for changing the tense, and every ongoing rewrite was refused as phrasing,
    because only past-tense verbs counted as a strong opening."""
    assert rewrite_quality_issue(
        "Contributing to internal dashboards in React for the support team to track tickets",
        "Building internal dashboards in React for the support team to track ticket volume",
        entry_is_ongoing=True,
    ) is None


def test_an_ongoing_bullet_that_already_opens_strong_is_still_refused_a_synonym_swap():
    assert "only changes phrasing" in rewrite_quality_issue(
        "Building internal dashboards in React for the support team to track ticket volume",
        "Developing internal dashboards in React for the support team to track ticket counts",
        entry_is_ongoing=True,
    )


# ── the planner and the checker must agree ───────────────────────────────────

def test_every_gap_has_a_legal_repair():
    """The trap behind the FSAE run: `bullet_quality_gaps` called the bullet weak for opening
    with "Contributing", and every verb that would have satisfied it is a word
    `ownership_inflation` refuses. A defect whose only repair is banned is worse than no
    defect — the model is sent to fix something and punished for trying."""
    shared_credit_bullet = FSAE

    gaps = bullet_quality_gaps(shared_credit_bullet)

    assert "it does not open with a concrete action verb" not in gaps


def test_a_gerund_opening_is_not_a_defect():
    """`bullet_is_already_strong` has always accepted gerunds through `opens_with_action`.
    Reading the two functions differently meant one called a bullet strong while the other
    called the same opening weak."""
    assert "it does not open with a concrete action verb" not in bullet_quality_gaps(
        "Building a Kubernetes deployment pipeline across three regions."
    )


# ── abstraction that refers to nothing ───────────────────────────────────────

def test_padding_cannot_license_itself_through_a_confirmed_skill():
    """The actual hole the reported bullet went through. `surfacing` skips the phrasing check
    so a confirmed skill can enter the bullet, and it only asks whether the skill's *name*
    appears — so "within the robotics pipeline" contained "robotics" and exempted itself."""
    proposed = ("Contributed C++ software to a ROS 2 autonomous-driving stack, focusing on "
                "cone perception and integrating perception components within the robotics "
                "pipeline.")

    assert abstraction_padding(FSAE, proposed) is not None
    assert rewrite_quality_issue(FSAE, proposed, surfacing="robotics") is not None


def test_an_abstract_noun_with_a_referent_is_fine():
    """The word is never the problem. Each of these says what it is talking about."""
    for original, proposed in [
        ("Ran deploys by hand.", "Built a CI/CD pipeline that deploys 12 services on merge."),
        ("Processed events nightly.", "Designed a data pipeline consuming Kafka events."),
        ("Worked on perception.",
         "Implemented VLP-16 point-cloud filtering and Euclidean clustering in the perception pipeline."),
    ]:
        assert abstraction_padding(original, proposed) is None, proposed


# ── what a reader would challenge ────────────────────────────────────────────

def test_the_doubt_matches_what_is_actually_missing():
    cases = [
        (FSAE, "ownership"),
        ("Worked on the frontend.", "mechanism"),
        ("Implemented VLP-16 point-cloud filtering for cone perception.", "impact"),
        ("Built a Kubernetes deployment pipeline across three regions, cutting deploy time 40%.",
         None),
    ]
    for text, expected in cases:
        doubt = recruiter_doubt(text)
        assert (doubt[0] if doubt else None) == expected, text


def test_a_version_number_is_not_a_quantity():
    """`numeric_claims` accepts "ROS 2" and "psycopg2" on purpose — a rewrite must not drop
    them. For "does this bullet say how big it was" they are noise, and treating them as
    measurements made a bullet with no scale at all look measured."""
    assert recruiter_doubt(
        "Engineered the backend with raw psycopg2 and pooled PostgreSQL connections."
    )[0] == "impact"


def test_strength_and_gaps_cannot_disagree():
    """They did. After `bullet_quality_gaps` stopped treating shared credit as a defect,
    `bullet_is_already_strong` still called the same bullet weak through `opens_with_action` —
    so it was sent to the model as a rewrite target while the brief listed nothing wrong with
    it, and every rewrite it produced was refused. One is now defined as the absence of the
    other, which is the only way they stay in step."""
    for text in [
        FSAE,
        "Built a Kubernetes deployment pipeline across three regions, cutting deploy time 40%.",
        "Building a Kubernetes deployment pipeline across three regions.",
        "Worked on the frontend.",
        "Helped out.",
        "",
    ]:
        assert bullet_is_already_strong(text) == (bool(text) and not bullet_quality_gaps(text)), text


# ── concision is allowed, and its limits are stated ──────────────────────────

def test_a_faithful_shorter_rewrite_is_no_longer_refused():
    """The 0.8 word-count floor and the cosmetic check both refused this: shorter tripped the
    floor, and adding no skill or number made it "only changes phrasing". Saying the same
    thing in fewer words is usually the improvement."""
    original = ("Contributing to a work-in-progress ROS 2 autonomous-driving stack in C++ for "
                "Pitt's FSAE EV driverless program, focused on cone-perception software.")
    proposed = "Contributing C++ cone perception to Pitt's FSAE EV ROS 2 autonomous-driving stack."

    assert rewrite_quality_issue(original, proposed) is None


def test_a_synonym_swap_at_the_same_length_is_still_refused():
    assert rewrite_quality_issue(
        "Deployed Kubernetes services across three regions.",
        "Deployed Kubernetes services through three regions.",
    ) is not None


def test_shortening_may_not_drop_a_technology():
    issue = rewrite_quality_issue(
        "Built a Python service backed by PostgreSQL for ingestion.",
        "Built a Python service for ingestion.",
    )
    assert issue is not None and "postgresql" in issue


def test_shortening_may_not_drop_a_number():
    issue = rewrite_quality_issue(
        "Cut deploy time 40% across three regions.",
        "Cut deploy time across regions.",
    )
    assert issue is not None and "measurable result" in issue


def test_shortening_that_deletes_a_program_name_is_refused():
    """"Pitt's FSAE EV" is neither a technology nor a number, and its loss used to pass
    unnoticed. The unfamiliar-name check catches capitalised names like these."""
    proposed = "Contributing to a ROS 2 autonomous-driving stack in C++, focused on cone perception."

    issue = rewrite_quality_issue(FSAE, proposed)
    assert issue is not None and "fsae" in issue and "pitt" in issue


def test_shortening_that_deletes_lowercase_context_passes_and_is_marked():
    """The limitation that remains, pinned rather than papered over. "work-in-progress" and
    "driverless" are lowercase, so no check sees them go. The edit is allowed and flagged,
    because the alternative is a warning-free proposal implying there is nothing to check."""
    proposed = "Contributing C++ cone perception to a ROS 2 stack for Pitt's FSAE EV."

    assert rewrite_quality_issue(FSAE, proposed) is None
    assert compression_only(FSAE, proposed) is True


def test_compression_only_means_compression_and_nothing_else():
    """Derived from the same signals the acceptance check reads. Computing it from a subset
    would mark an edit that also strengthened the verb as having nothing but length."""
    stronger_and_shorter = "Built cone-perception software in C++ for a ROS 2 stack."

    assert improvements(FSAE, stronger_and_shorter)["stronger_action"] is True
    assert compression_only(FSAE, stronger_and_shorter) is False


def test_worked_on_is_not_promoted_to_developed():
    """Three edits in the first real eval run did exactly this."""
    issue = rewrite_quality_issue(
        "Worked on backend services for the ordering platform.",
        "Developed backend services for the ordering platform.",
    )
    assert issue and "claims more of the work" in issue


def test_an_answer_that_says_they_built_it_licenses_saying_so():
    """Both asks in the second real eval run ended with no edit: the user answered "I built…",
    and the ownership check, reading only the two bullets, still refused "Built"."""
    original = "Worked on the order-status service for the ordering platform."
    proposed = "Built the order-status service for the ordering platform in Python."
    assert "claims more of the work" in rewrite_quality_issue(original, proposed)
    assert rewrite_quality_issue(
        original, proposed, answers=["I built the order-status service in Python."],
    ) is None


def test_concrete_answer_details_count_as_an_improvement():
    original = "Improved background processing so long runs recover after failures."
    answer = (
        "Moved execution to a separate worker service with expiring leases, fencing tokens, "
        "and per-step checkpoints."
    )
    proposed = (
        "Improved background processing with a separate worker service, expiring leases, "
        "fencing tokens, and per-step checkpoints so long runs recover after failures."
    )

    assert {"worker", "leases", "fencing", "checkpoints"} <= set(
        answer_detail_terms(original, proposed, [answer])
    )
    assert improvements(original, proposed, answers=[answer])["answer_detail"] is True
    assert rewrite_quality_issue(original, proposed, answers=[answer]) is None


def test_an_answer_does_not_license_a_cosmetic_rewrite():
    original = "Worked on background processing for long tailoring runs."
    answer = "Used a worker service with leases and checkpoints."
    proposed = "Handled background processing for long tailoring runs."

    assert answer_detail_terms(original, proposed, [answer]) == []
    issue = rewrite_quality_issue(original, proposed, answers=[answer])
    assert issue and "only changes phrasing" in issue


def test_answer_compound_omission_is_a_review_repair_not_a_default_truth_gate():
    original = "Added idempotency to avoid duplicate processing and repeated API calls."
    answer = (
        "Implemented reserve-before-spend idempotency for job-draft creation with "
        "stored-response replay."
    )
    proposed = (
        "Added idempotency for job-draft creation with stored-response replay to avoid "
        "duplicate processing."
    )

    assert omitted_answer_compounds(original, proposed, [answer]) == ["reserve-before-spend"]
    assert not any(
        item["code"] == "possible_answer_detail_omission"
        for item in rewrite_validation_findings(original, proposed, answers=[answer])[
            "repair_requests"
        ]
    )
    review = rewrite_validation_findings(
        original, proposed, answers=[answer], preserve_answer_compounds=True,
    )
    assert any(
        item["code"] == "possible_answer_detail_omission"
        and "reserve-before-spend" in item["message"]
        for item in review["repair_requests"]
    )


def test_answer_compound_preservation_needs_no_repair():
    original = "Added idempotency to avoid duplicate processing."
    answer = "Implemented reserve-before-spend idempotency for job-draft creation."
    proposed = "Implemented reserve-before-spend idempotency for job-draft creation."

    findings = rewrite_validation_findings(
        original, proposed, answers=[answer], preserve_answer_compounds=True,
    )
    assert not any(
        item["code"] == "possible_answer_detail_omission"
        for item in findings["repair_requests"]
    )


def test_avoiding_duplicates_is_an_existing_result_not_an_invented_one():
    findings = rewrite_validation_findings(
        "Added idempotency to avoid duplicate processing and repeated API calls.",
        "Implemented idempotency for job drafts, preventing duplicate processing and API calls.",
        answers=["Used reserve-before-spend idempotency for job-draft creation."],
    )

    assert "unclear_outcome_support" not in {
        item["code"] for item in findings["repair_requests"]
    }


# ── certainty-aware validation ───────────────────────────────────────────────

def test_validation_returns_every_quality_concern_instead_of_the_first():
    original = (
        "Contributing to a work-in-progress ROS 2 stack in C++ for Pitt's FSAE EV program, "
        "focused on cone-perception software."
    )
    findings = rewrite_validation_findings(
        original,
        "Developed software for a robotics pipeline, improving reliability.",
        entry_is_ongoing=True,
    )

    codes = {item["code"] for item in findings["repair_requests"]}
    assert {"ownership_change", "tense_change", "possible_skill_omission",
            "possible_name_omission", "unclear_outcome_support"} <= codes
    assert findings["hard_blocks"] == []
    assert all(item["message"] and item["evidence"] for item in findings["repair_requests"])


def test_unsupported_concrete_claim_is_a_hard_block():
    findings = rewrite_validation_findings(
        "Built a Python API.",
        "Built a Python and Redis API.",
        unsupported=["redis"],
    )

    assert findings["hard_blocks"] == [{
        "code": "unsupported_concrete_claim",
        "message": "redis does not appear in the evidence you cited",
        "evidence": "Checked against the cited bullet, current-run answers, and approved rewrites.",
    }]


def test_answer_backed_ownership_change_has_no_ownership_warning():
    findings = rewrite_validation_findings(
        "Worked on the order-status service.",
        "Built the order-status service in Python.",
        answers=["I built the order-status service in Python."],
    )

    assert "ownership_change" not in {
        item["code"] for item in findings["repair_requests"]
    }


def test_an_explicit_negative_answer_is_a_hard_contradiction():
    contradictions = explicit_answer_contradictions(
        "Built a Redis cache that handled 10 requests.",
        ["I did not use Redis, and it was not 10 requests; it was 5."],
    )
    findings = rewrite_validation_findings(
        "Worked on caching.",
        "Built a Redis cache that handled 10 requests.",
        answers=["I did not use Redis, and it was not 10 requests; it was 5."],
        contradictions=contradictions,
    )

    assert set(contradictions) == {"redis", "10"}
    assert {item["code"] for item in findings["hard_blocks"]} == {
        "explicit_answer_contradiction"
    }


def test_hard_contradiction_does_not_guess_from_ordinary_negative_language():
    assert explicit_answer_contradictions(
        "Built a Redis cache.",
        ["Redis was not only used for caching; it also backed rate limits."],
    ) == []
