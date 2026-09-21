"""Pure tests for deterministic resume composition."""

from services.composition import bullet_relevance, compose, entry_relevance, summarize


def requirement(name, importance="required", kind="skill"):
    return {"skill": name, "importance": importance, "type": kind}


def condition(*items, minimum=1, importance="required", operator="any_of"):
    return {
        "condition": {"operator": operator, "minimum": minimum, "items": list(items)},
        "importance": importance,
        "type": "skill",
    }


def bullet(bullet_id, text):
    return {"id": bullet_id, "text": text}


def entry(entry_id, kind, *bullets, title=None):
    return {"id": entry_id, "kind": kind, "title": title, "bullets": list(bullets)}


# ── bullet scoring ────────────────────────────────────────────────────────────

def test_required_outweighs_nice_to_have():
    scores = bullet_relevance(
        [requirement("Python"), requirement("Jira", "nice_to_have")],
        [bullet("b1", "Built services with Python"), bullet("b2", "Tracked work in Jira")],
    )
    assert scores["b1"] > scores["b2"]


def test_inferred_evidence_counts_for_ordering():
    scores = bullet_relevance(
        [requirement("CSS")],
        [bullet("b1", "Built the interface with Tailwind")],
    )
    assert scores["b1"] > 0


def test_related_experience_does_not_rank_a_bullet_at_all():
    """"Deployed cloud services" against an AWS requirement is related experience, not weak
    evidence of AWS. It used to score 0.4 and rank below the real match; it now scores
    nothing, so it is not promoted into the tailored resume as though it were relevant."""
    scores = bullet_relevance(
        [requirement("AWS")],
        [bullet("partial", "Deployed cloud services"), bullet("explicit", "Deployed to AWS")],
    )
    assert "partial" not in scores
    assert scores["explicit"] > 0


def test_unrelated_bullet_contributes_nothing():
    assert bullet_relevance(
        [requirement("Go")], [bullet("b1", "Designed a Figma prototype")]
    ) == {}


def test_one_bullet_accumulates_across_distinct_requirements():
    scores = bullet_relevance(
        [requirement("Python"), requirement("REST")],
        [bullet("b1", "Built Python REST services")],
    )
    assert scores["b1"] == 6.0


def test_condition_alternatives_do_not_multiply_one_requirement():
    scores = bullet_relevance(
        [condition("Python", "JavaScript")],
        [bullet("b1", "Built services with Python and JavaScript")],
    )
    assert scores["b1"] == 3.0


def test_eligibility_is_not_used_for_resume_ordering():
    assert bullet_relevance(
        [requirement("US citizenship", kind="eligibility")],
        [bullet("b1", "US citizenship")],
    ) == {}


def test_every_matching_bullet_is_scored_not_only_the_first_three():
    bullets = [bullet(f"b{i}", f"Built Python service {i}") for i in range(1, 6)]
    assert set(bullet_relevance([requirement("Python")], bullets)) == {
        "b1", "b2", "b3", "b4", "b5",
    }


def test_explicit_hit_does_not_hide_an_inferred_hit_in_another_bullet():
    scores = bullet_relevance(
        [requirement("CSS")],
        [bullet("explicit", "Wrote CSS"), bullet("inferred", "Built views with Tailwind")],
    )
    assert scores["explicit"] > scores["inferred"] > 0


# ── entry aggregation ────────────────────────────────────────────────────────

def test_one_strong_bullet_beats_many_weak_ones():
    scores = {"strong": 9.0, **{f"w{i}": 1.0 for i in range(20)}}
    assert entry_relevance(["strong"], scores) > entry_relevance(
        [f"w{i}" for i in range(20)], scores
    )


def test_a_few_strong_matches_reward_coverage():
    scores = {"a": 6.0, "b": 6.0, "c": 6.0, "d": 6.0}
    assert entry_relevance(["a", "b", "c"], scores) > entry_relevance(["d"], scores)


def test_fourth_and_later_bullets_cannot_pad_an_entry_score():
    scores = {f"b{i}": 2.0 for i in range(10)}
    assert entry_relevance(["b0", "b1", "b2"], scores) == entry_relevance(
        list(scores), scores
    )


def test_entry_with_no_evidence_scores_zero():
    assert entry_relevance(["x"], {}) == 0.0
    assert entry_relevance([], {"x": 5.0}) == 0.0


# ── ordering ─────────────────────────────────────────────────────────────────

def test_bullets_reorder_within_an_entry():
    plan = compose(
        [entry("e1", "project", bullet("b1", "Jira"), bullet("b2", "Python"))],
        [requirement("Python")],
    )
    assert plan["bullet_order"]["e1"] == ["b2", "b1"]


def test_ties_keep_the_resume_order():
    plan = compose(
        [entry("e1", "project", bullet("b1", "one"), bullet("b2", "two"))],
        [],
    )
    assert plan["bullet_order"]["e1"] == ["b1", "b2"]


def test_projects_reorder_by_relevance():
    plan = compose(
        [
            entry("p1", "project", bullet("b1", "Used Jira")),
            entry("p2", "project", bullet("b2", "Built Python APIs")),
        ],
        [requirement("Python")],
    )
    assert plan["entry_order"] == ["p2", "p1"]


def test_non_project_entries_keep_the_users_order():
    plan = compose(
        [
            entry("j1", "experience", bullet("b1", "Used Jira")),
            entry("j2", "experience", bullet("b2", "Built Python APIs")),
            entry("ed", "education", bullet("b3", "Studied Python")),
            entry("cert", "certificate", bullet("b4", "Python certificate")),
        ],
        [requirement("Python")],
    )
    assert plan["entry_order"] == ["j1", "j2", "ed", "cert"]


def test_projects_move_only_among_project_slots():
    plan = compose(
        [
            entry("p1", "project", bullet("b1", "Used Jira")),
            entry("j1", "experience", bullet("b2", "Supported users")),
            entry("p2", "project", bullet("b3", "Built Python APIs")),
        ],
        [requirement("Python")],
    )
    assert plan["entry_order"] == ["p2", "j1", "p1"]


def test_skill_entry_never_competes_with_project_entries():
    plan = compose(
        [
            entry("p1", "project", bullet("b1", "Used Jira")),
            entry("skills", "skill", bullet("s1", "Python")),
            entry("p2", "project", bullet("b2", "Built Python APIs")),
        ],
        [requirement("Python")],
    )
    assert plan["entry_order"] == ["p2", "skills", "p1"]


def test_skills_reorder_inside_the_skill_entry():
    plan = compose(
        [entry("skills", "skill", bullet("s1", "Figma"), bullet("s2", "Python"))],
        [requirement("Python")],
    )
    assert plan["bullet_order"]["skills"] == ["s2", "s1"]


def test_nothing_is_added_or_dropped():
    entries = [
        entry("p", "project", bullet("b1", "Jira"), bullet("b2", "Python")),
        entry("j", "experience", bullet("b3", "Support")),
    ]
    plan = compose(entries, [requirement("Python")])
    assert sorted(plan["entry_order"]) == ["j", "p"]
    assert sorted(b for ids in plan["bullet_order"].values() for b in ids) == ["b1", "b2", "b3"]


def test_summary_explains_only_visible_moves():
    entries = [
        entry("p1", "project", bullet("b1", "Jira"), title="Tracker"),
        entry("p2", "project", bullet("b2", "Python"), title="API"),
        entry("skills", "skill", bullet("s1", "Figma"), bullet("s2", "Python")),
    ]
    plan = compose(entries, [requirement("Python")])
    summary = summarize(entries, plan)
    assert summary == {
        "changed": True,
        "moved_bullets": 0,
        "promoted_projects": ["API"],
        "promoted_bullets": [],
        "promoted_skills": ["Python"],
        "reordered_skills": 2,
    }


def test_summary_reports_only_the_bullet_that_moved_up():
    entries = [entry(
        "p1",
        "project",
        bullet("b1", "Tracked work in Jira"),
        bullet("b2", "Built Python APIs"),
        title="API project",
    )]
    summary = summarize(entries, compose(entries, [requirement("Python")]))

    assert summary["moved_bullets"] == 2
    assert summary["promoted_bullets"] == [{
        "entry": "API project",
        "text": "Built Python APIs",
        "from": 2,
        "to": 1,
    }]


def test_empty_requirements_change_nothing():
    entries = [
        entry("p2", "project", bullet("b2", "two")),
        entry("p1", "project", bullet("b1", "one")),
    ]
    plan = compose(entries, [])
    assert plan["entry_order"] == ["p2", "p1"]
    assert summarize(entries, plan)["changed"] is False
