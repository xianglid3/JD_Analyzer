"""Cost accounting and the daily spend cap."""

import pytest

from services import usage
from services.usage import QuotaExceeded, check_quota, record, report, spent_today


USER = {"username": "usageuser", "password": "pw123456"}


@pytest.fixture
def user_id(client, _db):
    client.post("/api/auth/signup", json=USER)
    client.post("/api/auth/login", json=USER)
    return client.get("/api/auth/me").get_json()["id"]


def test_a_call_is_recorded_with_its_cost(_db, user_id):
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 1200)

    with _db.cursor() as cur:
        cur.execute("SELECT kind, cost_usd, latency_ms FROM llm_calls WHERE user_id = %s", (user_id,))
        kind, cost, latency = cur.fetchone()

    assert kind == "job_analysis"
    assert float(cost) == 0.15
    assert latency == 1200


def test_spend_accumulates_across_calls(_db, user_id):
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)
    record(user_id, "resume_parse", "gpt-4o-mini", 1_000_000, 0, 100)

    with _db.cursor() as cur:
        assert spent_today(cur, user_id) == pytest.approx(0.30)


def test_quota_blocks_once_the_day_is_spent(_db, user_id, monkeypatch):
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", 0.20)
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)

    check_quota(user_id)                       # 0.15 of 0.20 — still allowed

    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)
    with pytest.raises(QuotaExceeded):
        check_quota(user_id)


def test_yesterdays_spending_does_not_count(_db, user_id, monkeypatch):
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", 0.10)
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)
    with _db.cursor() as cur:
        cur.execute("UPDATE llm_calls SET created_at = now() - interval '2 days' WHERE user_id = %s", (user_id,))
    _db.commit()

    check_quota(user_id)                       # the cap is per day, not forever


def test_one_users_spending_does_not_limit_another(_db, user_id, client, monkeypatch):
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", 0.10)
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)

    client.post("/api/auth/logout")
    client.post("/api/auth/signup", json={"username": "usageother", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "usageother", "password": "pw123456"})
    other_id = client.get("/api/auth/me").get_json()["id"]

    check_quota(other_id)


def test_analysis_is_refused_when_the_budget_is_gone(client, _db, user_id, monkeypatch):
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", 0.01)
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)
    called = []
    monkeypatch.setattr("routes.jobs.analyze_job_description", lambda *a, **k: called.append(1))

    response = client.post(
        "/api/jobs/drafts",
        json={"description": "x" * 200},
        headers={"Idempotency-Key": "11111111-1111-1111-1111-111111111111"},
    )

    assert response.status_code == 429
    assert called == [], "the model was called after the budget ran out"


def test_a_refused_request_can_be_retried_with_the_same_key(client, _db, user_id, monkeypatch):
    """The idempotency reservation is released, or the user is stuck once they top up."""
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", 0.01)
    record(user_id, "job_analysis", "gpt-4o-mini", 1_000_000, 0, 100)
    key = {"Idempotency-Key": "22222222-2222-2222-2222-222222222222"}
    client.post("/api/jobs/drafts", json={"description": "x" * 200}, headers=key)

    with _db.cursor() as cur:
        cur.execute("SELECT count(*) FROM idempotency_requests WHERE user_id = %s", (user_id,))
        assert cur.fetchone()[0] == 0


def test_report_gives_percentiles_per_kind(_db, user_id):
    for latency in (100, 200, 300, 400, 1000):
        record(user_id, "job_analysis", "gpt-4o-mini", 1000, 100, latency)

    with _db.cursor() as cur:
        data = report(cur, days=30)

    kind = next(row for row in data["by_kind"] if row["kind"] == "job_analysis")
    assert kind["calls"] == 5
    assert kind["p50_ms"] == 300
    assert kind["p95_ms"] == pytest.approx(880, abs=1)
    assert data["by_user"][0]["username"] == USER["username"]
