"""The skill relationship graph — what counts as evidence of what.

The direction of every edge is the safety property here: reading one backwards is how a
system starts claiming skills the candidate never showed.
"""

from services.resume_search import expand_query
from services.skill_graph import IMPLIES, MAX_DEPTH, evidence_for, implied_by, rewrite_implied_by


def test_specific_implies_general():
    assert "css" in implied_by("tailwind")
    assert "javascript" in implied_by("react")
    assert "python" in implied_by("fastapi")


def test_general_never_implies_specific():
    """The whole point. Knowing CSS says nothing about Tailwind."""
    assert implied_by("css") == []
    assert "react" not in implied_by("javascript")
    assert "fastapi" not in implied_by("python")


def test_edges_compose():
    # supabase → postgresql → sql → database
    assert set(["postgresql", "sql", "database"]) <= set(implied_by("supabase"))


def test_evidence_lookup_is_the_reverse_direction():
    assert "tailwind" in evidence_for("css")
    assert "pytest" in evidence_for("testing")
    assert "github" in evidence_for("version control")


def test_a_requirement_search_reaches_its_evidence():
    """The BUG-070 case: the JD says CSS, the resume says Tailwind."""
    assert "tailwind" in expand_query("CSS")
    assert "react" in expand_query("JavaScript")
    assert "github" in expand_query("Git")


def test_search_expansion_still_leads_with_the_literal_term():
    assert expand_query("CSS")[0] == "CSS"


def test_graph_has_no_cycles_within_the_depth_cap():
    """A cycle would make implied_by silently truncate instead of being wrong loudly."""
    for skill in IMPLIES:
        reached = implied_by(skill)
        assert skill not in reached, f"{skill} implies itself"
        assert len(reached) < 40, f"{skill} expands implausibly far"


def test_every_edge_target_is_a_lowercase_bare_name():
    """Targets are compared against normalized skills, so casing drift breaks matching silently."""
    for specific, parents in IMPLIES.items():
        assert specific == specific.lower()
        for parent in parents:
            assert parent == parent.lower()


def test_depth_cap_is_respected():
    assert MAX_DEPTH >= 2       # supabase → postgresql → sql needs at least two hops


def test_rewrite_permissions_are_strict_and_one_hop():
    assert "css" in rewrite_implied_by("tailwind")
    assert "sql" in rewrite_implied_by("postgresql")
    assert "javascript" not in rewrite_implied_by("typescript")
    assert "oop" not in rewrite_implied_by("cpp")
    assert "oop" not in rewrite_implied_by("google test")
