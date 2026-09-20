"""What a rewrite may and may not say.

`test_tailoring_agent.py` covers the tool boundary; this covers the rules underneath it.
"""

from services.claim_check import (
    numeric_claims,
    ownership_inflation,
    rewrite_quality_issue,
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
    """`surfacing` exempts the phrasing check, and that exemption is what let the reported
    bullet through: the padding clause "within the robotics pipeline" contained the word
    "robotics" and so supplied its own exemption. The factual checks run either way."""
    proposed = ("Contributed C++ software to a ROS 2 autonomous-driving stack, focusing on "
                "cone perception and integrating perception components within the robotics "
                "pipeline.")

    assert rewrite_quality_issue(FSAE, proposed, surfacing="robotics") is None
    assert rewrite_quality_issue(
        FSAE, proposed, surfacing="robotics", entry_is_ongoing=True,
    ) is not None


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
