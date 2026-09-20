"""What a rewrite may and may not say.

`test_tailoring_agent.py` covers the tool boundary; this covers the rules underneath it.
"""

from services.claim_check import numeric_claims, ownership_inflation, rewrite_quality_issue


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
