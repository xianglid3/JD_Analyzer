"""Evidence search — what the tailoring agent calls to find support for a requirement."""

import pytest

from db import get_cursor
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services.skill_graph import evidence_for, implied_by
from services.resume_search import expand_query, search_resume_bullets


BULLETS = [
    "Deployed services to Kubernetes across three regions",
    "Built an ingestion pipeline in Python processing 2M events daily",
    "Wrote integration tests and set up CI/CD in GitHub Actions",
    "Led migration from MySQL to PostgreSQL with zero downtime",
]


@pytest.fixture
def user_id(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE resume_bullets, resume_entries, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('searchuser', 'x') RETURNING id")
        uid = cur.fetchone()[0]
        save_resume_evidence(cur, uid, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Backend Intern", bullets=BULLETS)
        ]))
        return uid


def texts(results):
    return [r["text"] for r in results]


# ── alias expansion, no DB ───────────────────────────────────────────────────

def test_expand_adds_alias_siblings():
    assert "kubernetes" in expand_query("k8s")
    assert "k8s" in expand_query("Kubernetes")


def test_expand_handles_multi_word_requirements():
    assert "postgresql" in expand_query("Postgres experience")


def test_expand_keeps_the_original_phrase_first():
    assert expand_query("Postgres")[0] == "Postgres"


def test_expand_of_blank_is_empty():
    assert expand_query("   ") == []


# ── search ───────────────────────────────────────────────────────────────────

def test_finds_the_relevant_bullet(_db, user_id):
    with _db.cursor() as cur:
        results = search_resume_bullets(cur, user_id, "Python")
    assert "Built an ingestion pipeline in Python processing 2M events daily" in texts(results)


def test_alias_finds_the_bullet_that_spells_it_differently(_db, user_id):
    """The JD says k8s; the resume says Kubernetes. This is why aliases are in the query path."""
    with _db.cursor() as cur:
        results = search_resume_bullets(cur, user_id, "k8s")
    assert "Deployed services to Kubernetes across three regions" in texts(results)


def test_stemming_matches_a_different_word_form(_db, user_id):
    with _db.cursor() as cur:
        results = search_resume_bullets(cur, user_id, "testing")
    assert "Wrote integration tests and set up CI/CD in GitHub Actions" in texts(results)


def test_results_carry_ids_and_entry_context(_db, user_id):
    with _db.cursor() as cur:
        results = search_resume_bullets(cur, user_id, "Python")
    hit = results[0]
    assert hit["bullet_id"] and hit["organization"] == "Acme"
    assert hit["entry_title"] == "Backend Intern" and hit["kind"] == "experience"


def test_missing_experience_returns_nothing(_db, user_id):
    """The agent needs a real empty result — this is what makes flag_gap honest."""
    with _db.cursor() as cur:
        assert search_resume_bullets(cur, user_id, "Terraform") == []


def test_empty_query_returns_nothing(_db, user_id):
    with _db.cursor() as cur:
        assert search_resume_bullets(cur, user_id, "  ") == []
        assert search_resume_bullets(cur, user_id, None) == []


def test_limit_is_bounded(_db, user_id):
    with _db.cursor() as cur:
        assert len(search_resume_bullets(cur, user_id, "the", limit=2)) <= 2
        assert len(search_resume_bullets(cur, user_id, "the", limit=999)) <= 10


def test_search_never_crosses_users(_db, user_id):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('searchother', 'x') RETURNING id")
        other_id = cur.fetchone()[0]
        save_resume_evidence(cur, other_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", bullets=["Deployed services to Kubernetes"])
        ]))

        mine = search_resume_bullets(cur, user_id, "Kubernetes")
        theirs = search_resume_bullets(cur, other_id, "Kubernetes")

    assert texts(mine) == ["Deployed services to Kubernetes across three regions"]
    assert texts(theirs) == ["Deployed services to Kubernetes"]


# ── the entry header is evidence too ────────────────────────────────────────

def test_a_skill_named_only_in_the_project_title_still_finds_its_bullets(_db, user_id):
    """A project called "Motor Control (C++, Google Test)" names skills no bullet repeats."""
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(
                kind="project",
                title="Motor Control System (C++, Google Test)",
                bullets=["Implemented fault handling for the drive controller"],
            )
        ]))
        results = search_resume_bullets(cur, user_id, "Google Test")

    assert texts(results) == ["Implemented fault handling for the drive controller"]
    # flagged, because the bullet itself never says it — a rewrite must stay inside the bullet
    assert results[0]["matched_via"] == "entry_header"


def test_a_bullet_hit_outranks_a_header_hit(_db, user_id):
    with _db.cursor() as cur:
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", title="Kubernetes Platform",
                                  bullets=["Wrote the deployment runbook"]),
            ResumeEntryExtraction(kind="experience", organization="Acme",
                                  bullets=["Deployed services to Kubernetes across three regions"]),
        ]))
        results = search_resume_bullets(cur, user_id, "kubernetes")

    assert results[0]["matched_via"] == "bullet"
    assert "Kubernetes across three regions" in results[0]["text"]


# ── search has to be able to find whatever matching found ────────────────────
# A requirement matched PARTIAL because a bullet says "cloud". The planner then handed that
# candidate to the agent, the agent searched "aws", got nothing, and had no legal move left —
# so it invented a bullet id and spent the rest of the run being refused. Matching walked the
# graph in a direction search did not.

def test_search_reaches_the_general_evidence_a_partial_match_used():
    assert "cloud" in expand_query("aws")


def test_search_still_prefers_specific_evidence_over_general():
    terms = expand_query("aws")
    # dynamodb IS aws; cloud only might be — the stronger signal has to rank first
    assert terms.index("dynamodb") < terms.index("cloud")


@pytest.mark.parametrize("requirement", ["aws", "css", "sql", "testing", "api"])
def test_every_state_the_matcher_can_reach_is_searchable(requirement):
    """The invariant behind both directions: anything `evaluate_requirement` can match on,
    `expand_query` must also look for. Otherwise the fit engine promises evidence the agent
    cannot retrieve."""
    terms = set(expand_query(requirement))
    assert set(evidence_for(requirement)) <= terms          # INFERRED
    assert set(implied_by(requirement)) <= terms            # PARTIAL


def test_a_partial_match_is_retrievable_end_to_end(_db):
    """The real shape of the bug, through the database rather than the term list."""
    from services.skill_evidence import evaluate_requirement

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('partial', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_entries (user_id, kind, title, sort_order)
            VALUES (%s, 'project', 'Infra', 0) RETURNING id
            """,
            (user_id,),
        )
        entry_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_bullets (entry_id, user_id, text, content_hash, sort_order)
            VALUES (%s, %s, 'Ran cloud deployments for the team', 'h1', 0)
            """,
            (entry_id, user_id),
        )

    bullets = [{"bullet_id": None, "text": "Ran cloud deployments for the team"}]
    assert evaluate_requirement("aws", bullets)["state"] == "PARTIAL"

    with get_cursor() as cur:
        found = search_resume_bullets(cur, user_id, "aws", limit=5)

    assert [row["text"] for row in found] == ["Ran cloud deployments for the team"]


def test_search_does_not_depend_on_someone_else_loading_the_graph(_db):
    """A learned relation lives in a process-wide cache that has to be read from the database.
    Search assumed that had already happened, so in a worker that had not scored anything yet
    the same query returned nothing — which strands the agent and burns the run."""
    from services import skill_relations

    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('coldcache', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_entries (user_id, kind, title, sort_order)
            VALUES (%s, 'project', 'Controller', 0) RETURNING id
            """,
            (user_id,),
        )
        entry_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_bullets (entry_id, user_id, text, content_hash, sort_order)
            VALUES (%s, %s, 'Built a state machine in Zig', 'h1', 0)
            """,
            (entry_id, user_id),
        )
        cur.execute(
            """
            INSERT INTO skill_relations (specific, general, rewriteable, source)
            VALUES ('zig', 'systems programming', false, 'model')
            """
        )

    skill_relations.reset()                 # a worker that has scored nothing yet

    with get_cursor() as cur:
        found = search_resume_bullets(cur, user_id, "systems programming", limit=3)

    assert [row["text"] for row in found] == ["Built a state machine in Zig"]
