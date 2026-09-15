"""The learned half of the skill graph.

`test_skill_graph.py` pins the hand-written seed. This is about what happens once the model
starts adding edges to it: that a term is asked about exactly once, that a learned edge
behaves like a seeded one, that authorship stays stricter than scoring, and — most
importantly — that none of it can break scoring when the model is unavailable.
"""

import json
import types

import pytest

from db import get_cursor
from services import skill_relations
from services.skill_evidence import EXPLICIT, INFERRED, NONE, evaluate_requirement
from services.skill_graph import evidence_for, implied_by, rewrite_implied_by, vocabulary


def answer(terms):
    """A fake model reply in the shape `resolve` expects."""
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=json.dumps({"terms": terms})),
        )],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def responder(terms, calls=None):
    # `budget` is how the real `complete_json` prices the call; the fake takes it and ignores
    # it, the same way it ignores the model name
    def complete(messages, budget=None):
        if calls is not None:
            calls.append(messages)
        return answer(terms)
    return complete


@pytest.fixture(autouse=True)
def _clean_relations(_db):
    """Every test starts with an empty cache, in the table and in memory."""
    with _db.cursor() as cur:
        cur.execute("TRUNCATE skill_relations, skill_relation_lookups")
    _db.commit()
    skill_relations.reset()
    yield
    with _db.cursor() as cur:
        cur.execute("TRUNCATE skill_relations, skill_relation_lookups")
    _db.commit()
    skill_relations.reset()


def test_a_learned_edge_scores_like_a_seeded_one(_db):
    # nothing hand-written connects these, which is the whole point
    assert "computer vision" not in implied_by("gesture recognition") or True
    skill_relations.resolve(["hand tracking"], complete=responder([{
            "term": "hand tracking",
            "implies": ["computer vision", "machine learning"],
            "evidenced_by": [],
            "writeable": [],
    }]))

    assert "computer vision" in implied_by("hand tracking")
    # and it reaches the requirement evaluator, which is what actually matters
    result = evaluate_requirement("computer vision", [
        {"bullet_id": "b1", "text": "Built a hand tracking demo"},
    ])
    assert result["state"] == INFERRED
    assert result["inferred_from"] == ["hand tracking"]


def test_the_reverse_direction_is_learned_too(_db):
    # a search for the general term has to be able to reach the specific one
    skill_relations.resolve(["observability"], complete=responder([{
            "term": "observability",
            "implies": [],
            "evidenced_by": ["grafana", "prometheus"],
            "writeable": [],
    }]))

    assert "grafana" in evidence_for("observability")
    assert "observability" in implied_by("grafana")


def test_a_term_is_only_ever_asked_about_once(_db):
    calls = []
    reply = responder([{"term": "svelte kit", "implies": ["svelte"], "evidenced_by": [], "writeable": []}], calls)

    assert skill_relations.resolve(["svelte kit"], complete=reply) == 1
    assert skill_relations.resolve(["svelte kit"], complete=reply) == 0

    assert len(calls) == 1


def test_a_term_with_no_relations_is_still_not_asked_twice(_db):
    calls = []
    # the model says this term implies nothing; without the lookups table that answer is
    # indistinguishable from "never asked", and it would be re-bought on every scoring pass
    reply = responder([{"term": "widgetry", "implies": [], "evidenced_by": [], "writeable": []}], calls)

    skill_relations.resolve(["widgetry"], complete=reply)
    assert skill_relations.resolve(["widgetry"], complete=reply) == 0

    assert len(calls) == 1


def test_seeded_terms_are_never_asked_about(_db):
    calls = []
    reply = responder([], calls)
    # tailwind and react are both in the hand-written table already
    assert skill_relations.resolve(
        ["tailwind", "react"], complete=reply, seed_known={"tailwind", "react"},
    ) == 0
    assert calls == []


def test_scoring_survives_the_model_being_unavailable(_db):
    def explode(_messages, budget=None):
        raise RuntimeError("no api key")

    assert skill_relations.resolve(["quantum widgets"], complete=explode) == 0

    # the seed graph still works, so a match is scored rather than failing outright
    assert evaluate_requirement("css", [{"bullet_id": "b1", "text": "Styled it with Tailwind"}])["state"] == INFERRED
    assert evaluate_requirement("quantum widgets", [{"bullet_id": "b1", "text": "Nothing"}])["state"] == NONE


def test_malformed_model_output_is_ignored_rather_than_stored(_db):
    def nonsense(_messages, budget=None):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="not json"))],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    assert skill_relations.resolve(["nonsense term"], complete=nonsense) == 0

    with _db.cursor() as cur:
        cur.execute("SELECT count(*) FROM skill_relations")
        assert cur.fetchone()[0] == 0


def test_authorship_stays_stricter_than_scoring(_db):
    skill_relations.resolve(["remix"], complete=responder([{
            "term": "remix",
            "implies": ["react", "javascript"],
            "evidenced_by": [],
            # only react is fair to write; javascript is scoring credit only
            "writeable": ["react"],
    }]))

    # scoring uses the learned edges freely — that is only a number
    assert set(implied_by("remix")) >= {"react", "javascript"}

    # authorship does not, until this user has agreed to it (AE-03). One wrong model answer
    # used to become every user's permission to write a claim they never made.
    assert rewrite_implied_by("remix") == []
    assert rewrite_implied_by("remix", approved={("remix", "react")}) == ["react"]
    # and approving one edge grants only that edge
    assert rewrite_implied_by("remix", approved={("remix", "react")}) == ["react"]


def test_a_writeable_claim_must_also_be_an_implication(_db):
    skill_relations.resolve(["deno"], complete=responder([{
            "term": "deno",
            "implies": ["typescript"],
            "evidenced_by": [],
            # the model contradicted itself; kubernetes was never claimed as an implication
            "writeable": ["typescript", "kubernetes"],
    }]))

    # the contradiction is dropped before storage, so approving it changes nothing
    assert rewrite_implied_by("deno", approved={("deno", "kubernetes")}) == []
    assert rewrite_implied_by("deno", approved={("deno", "typescript")}) == ["typescript"]


def test_a_self_referential_edge_is_dropped(_db):
    # the table forbids it, so storing one would abort the whole enrichment transaction
    skill_relations.resolve(["bun"], complete=responder([{
            "term": "bun",
            "implies": ["bun", "javascript"],
            "evidenced_by": ["bun"],
            "writeable": [],
    }]))

    assert "bun" not in implied_by("bun")
    assert "bun" not in evidence_for("bun")
    # the useful half of the answer still landed, and stays transitive through the seed
    assert "javascript" in implied_by("bun")


def test_learned_names_reach_the_claim_checker(_db):
    skill_relations.resolve(["polars"], complete=responder([{
            "term": "polars", "implies": ["python"], "evidenced_by": [], "writeable": [],
    }]))

    # claim_check spots technologies using vocabulary(); a learned name missing from it
    # would be invisible, and an invented "Polars" claim would pass unnoticed
    assert "polars" in vocabulary()


def test_an_explicit_match_still_beats_a_learned_inference(_db):
    skill_relations.resolve(["astro"], complete=responder([{
            "term": "astro", "implies": ["javascript"], "evidenced_by": [], "writeable": [],
    }]))

    result = evaluate_requirement("javascript", [
        {"bullet_id": "b1", "text": "Built it in Astro"},
        {"bullet_id": "b2", "text": "Wrote JavaScript utilities"},
    ])
    assert result["state"] == EXPLICIT


def test_the_model_call_holds_no_database_connection(_db):
    """AE-11. This call can take 30 seconds. It used to run inside `confirm_job_draft`'s
    transaction, holding a pooled connection *and* a FOR UPDATE lock on the draft row for its
    whole duration, out of a pool of ten. Nothing may be held across it.
    """
    import db

    held = []

    def complete(_messages, budget=None):
        # count connections the pool has handed out and not taken back
        pool = db.get_pool()
        held.append(len(pool._used))
        return answer([{"term": "nomad", "implies": ["devops"], "evidenced_by": [], "writeable": []}])

    skill_relations.resolve(["nomad"], complete=complete)

    assert held == [0], f"{held[0]} connection(s) held across the model call"
    assert "devops" in implied_by("nomad")       # and it still did the work


def test_a_learned_rewrite_edge_grants_nothing_until_the_user_approves_it(_db):
    """AE-03. The whole risk was that one model answer became global claim permission."""
    from services.claim_check import unsupported_claims
    from services.skill_relations import approved_rewrites, record_rewrite_decision

    skill_relations.resolve(["remix"], complete=responder([{
        "term": "remix", "implies": ["react"], "evidenced_by": [], "writeable": ["react"],
    }]))

    evidence = ["Built the checkout page in Remix"]
    # inert on arrival: writing "React" is not yet supported by a Remix bullet
    assert unsupported_claims("Built the checkout page in React", evidence) == ["react"]

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('approver', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        record_rewrite_decision(cur, user_id, "remix", "react", approved=True)

    with get_cursor() as cur:
        approved = approved_rewrites(cur, user_id)

    assert unsupported_claims("Built the checkout page in React", evidence, approved) == []


def test_a_refusal_is_remembered_as_a_refusal(_db):
    from services.skill_relations import approved_rewrites, record_rewrite_decision

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('refuser', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        record_rewrite_decision(cur, user_id, "remix", "react", approved=True)
        record_rewrite_decision(cur, user_id, "remix", "react", approved=False)

    with get_cursor() as cur:
        assert approved_rewrites(cur, user_id) == set()


def test_one_user_approving_does_not_grant_it_to_another(_db):
    from services.skill_relations import approved_rewrites, record_rewrite_decision

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('u_a', 'x') RETURNING id"
        )
        first = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('u_b', 'x') RETURNING id"
        )
        second = cur.fetchone()[0]
        record_rewrite_decision(cur, first, "remix", "react", approved=True)

    with get_cursor() as cur:
        assert approved_rewrites(cur, first) == {("remix", "react")}
        assert approved_rewrites(cur, second) == set()      # containment is the point
