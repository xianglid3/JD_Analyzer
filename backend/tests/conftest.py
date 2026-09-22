import os

# Env the app needs at import time. setdefault → CI or a real shell can override.
os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")
os.environ.setdefault("SUPABASE_URL", "postgresql://postgres:postgres@localhost:5432/jd_test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-not-a-real-key")  # ≥32 bytes → no InsecureKeyLength warning

# Not setdefault: the suite is never a production deployment, whatever the developer's .env
# says. Without this, a .env carrying the production flags makes the app refuse to import and
# the whole suite fails to collect — which is the guard working, in the wrong place.
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_DOMAIN"] = ""
os.environ["SENTRY_DSN"] = ""          # test runs must never report to a real project
# Signup friction is deployment policy, not application behaviour. Inheriting it from a
# developer's .env makes every test that creates an account fail, which is how one variable
# turned into a hundred red tests. The tests that care set these on the module themselves.
os.environ["SIGNUP_INVITE_CODE"] = ""
os.environ["MAX_SIGNUPS_PER_DAY"] = "0"
# and the caps: a local .env is free to carry a tiny ceiling for testing without the whole
# suite refusing to spend
os.environ["LLM_DAILY_USD"] = "1.00"
os.environ["LLM_GLOBAL_DAILY_USD"] = "10.00"

import pathlib
import psycopg2
import pytest

SCHEMA_SQL = (pathlib.Path(__file__).resolve().parent.parent / "schema.sql").read_text()
DSN = os.environ["SUPABASE_URL"]
TABLES = "skill_relations, skill_relation_lookups, llm_calls, llm_daily_budgets, llm_global_budget, tailoring_edit_details, tailoring_detail_requests, evidence_links, tailoring_edit_bullets, proposed_edits, tool_calls, tailoring_runs, resume_bullets, resume_entries, resume_headers, idempotency_requests, job_analysis_drafts, refresh_tokens, jobs, resumes, users"


def _looks_like_test_db(dsn):
    # safety: never wipe a real database — only localhost or a *test* database
    d = dsn.lower()
    return "localhost" in d or "127.0.0.1" in d or "test" in d


@pytest.fixture(scope="session")
def _db():
    """Session-wide connection to a throwaway Postgres, with the schema applied.
    Skips (not fails) the DB tests when no test database is reachable — e.g. a
    local `pytest` run without one. In CI the `postgres` service provides it."""
    if not _looks_like_test_db(DSN):
        pytest.skip("refusing to run DB tests against a non-test SUPABASE_URL")
    try:
        conn = psycopg2.connect(DSN)
    except psycopg2.OperationalError:
        pytest.skip("no test database reachable (set SUPABASE_URL to a throwaway Postgres)")

    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute(SCHEMA_SQL)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _no_live_openai(monkeypatch):
    """Nothing in the suite may reach OpenAI.

    `backend/.env` carries a real key and `load_dotenv()` overrides the dummy set above, so
    an unmocked call spends money and makes the test depend on the network. Tests that need
    a model response patch the analyze_* function; anything else fails here instead.
    """
    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "a test called OpenAI — patch the analyze_* function it goes through"
        )

    # both modules build their own client, and only patching one leaves the other spending
    import services.openai_services as openai_services
    import services.tailoring_agent as tailoring_agent

    monkeypatch.setattr(openai_services.client.chat.completions, "create", refuse)
    monkeypatch.setattr(tailoring_agent.client.chat.completions, "create", refuse)


@pytest.fixture
def client(_db):
    # clean slate before each test
    with _db.cursor() as cur:
        cur.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE;")

    from app import app
    from extensions import limiter
    app.config["TESTING"] = True
    limiter.reset()
    with app.test_client() as c:
        yield c


@pytest.fixture
def insert_job(_db):
    """Insert a job row directly (bypasses the OpenAI-backed create endpoint)."""
    def _insert(
        user_id,
        description="x" * 60,
        title=None,
        company_name=None,
        status="saved",
        match_score=None,
    ):
        with _db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    user_id, raw_description, title, company_name, status, match_score
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, description, title, company_name, status, match_score),
            )
            return str(cur.fetchone()[0])
    return _insert


@pytest.fixture(autouse=True)
def _run_tailoring_inline(monkeypatch):
    """Drive tailoring runs in-process for the whole suite.

    Production hands a run to a separate worker, so the route only writes the row. A test
    that asserts on what a run produced has to be the worker too — this is the same
    execution path, lease and fencing token included, in one process.
    """
    monkeypatch.setenv("TAILORING_INLINE", "1")


@pytest.fixture(autouse=True)
def _isolate_skill_relations():
    """The learned skill graph is cached process-wide, because a relation is a fact about the
    world rather than about one user. That makes it leak between tests, so it is dropped
    around each one — a stray edge learned in one test would silently change another's score.
    """
    from services import skill_relations

    skill_relations.reset()
    yield
    skill_relations.reset()


@pytest.fixture(autouse=True)
def _diagnosis_off(monkeypatch):
    """Most tests are about the editing loop and script only its model turns. The diagnosis
    step is its own paid call; tests that exercise it switch it back on and script it."""
    from services import bullet_diagnosis

    monkeypatch.setattr(bullet_diagnosis, "ENABLED", False)
