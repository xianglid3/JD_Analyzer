"""Evidence search — what the tailoring agent calls to find support for a requirement."""

import pytest

from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
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
