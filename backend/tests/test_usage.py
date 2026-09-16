"""Cost accounting and the daily spend cap."""

import pytest

from db import get_cursor
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
    # the ledger is keyed by date, so an older day is a different row rather than an older
    # timestamp — this is what "per day" means now that the cap is enforced on a row
    with _db.cursor() as cur:
        cur.execute(
            "UPDATE llm_daily_budgets SET budget_date = current_date - 2 WHERE user_id = %s",
            (user_id,),
        )
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


# ── the reservation ledger (AE-10) ───────────────────────────────────────────

def test_a_reservation_is_charged_before_the_call_not_after(_db, user_id):
    """Read-then-spend let two requests both see room and both spend. The ceiling is claimed
    up front, so the second one sees the first one's reservation."""
    reservation = usage.reserve(user_id, "job_analysis", "gpt-4o-mini")

    with _db.cursor() as cur:
        assert usage.spent_today(cur, user_id) == pytest.approx(usage.ceiling_cost("job_analysis"))
        cur.execute("SELECT outcome FROM llm_calls WHERE id = %s", (reservation["id"],))
        assert cur.fetchone()[0] == "reserved"


def test_finalizing_releases_what_the_call_did_not_use(_db, user_id):
    reservation = usage.reserve(user_id, "job_analysis", "gpt-4o-mini")
    usage.finalize(reservation, 1_000_000, 0, 120)      # $0.15 of a much larger ceiling

    with _db.cursor() as cur:
        assert usage.spent_today(cur, user_id) == pytest.approx(0.15)
        cur.execute("SELECT outcome, cost_usd FROM llm_calls WHERE id = %s", (reservation["id"],))
        outcome, cost = cur.fetchone()
    assert outcome == "ok" and float(cost) == pytest.approx(0.15)


def test_two_concurrent_reservations_cannot_both_fit(_db, user_id, monkeypatch):
    """The race the old SUM() could not stop."""
    monkeypatch.setattr(usage, "DAILY_LIMIT_USD", usage.ceiling_cost("job_analysis") * 1.5)

    usage.reserve(user_id, "job_analysis", "gpt-4o-mini")
    with pytest.raises(QuotaExceeded):
        usage.reserve(user_id, "job_analysis", "gpt-4o-mini")


def test_a_failed_call_is_charged_rather_than_forgiven(_db, user_id):
    """The prompt went out. Pretending a timeout was free is how a budget is overrun."""
    reservation = usage.reserve(user_id, "tailoring_step", "gpt-4o-mini")
    usage.finalize(reservation, 0, 0, 60_000, outcome="timeout")

    with _db.cursor() as cur:
        assert usage.spent_today(cur, user_id) == pytest.approx(reservation["reserved"])


def test_a_reservation_whose_caller_died_is_reconciled(_db, user_id):
    reservation = usage.reserve(user_id, "job_analysis", "gpt-4o-mini")
    with _db.cursor() as cur:
        cur.execute("UPDATE llm_calls SET created_at = now() - interval '1 hour' WHERE id = %s",
                    (reservation["id"],))
    _db.commit()

    with get_cursor(commit=True) as cur:
        settled = usage.release_stale_reservations(cur)

    assert str(reservation["id"]) in settled
    with _db.cursor() as cur:
        cur.execute("SELECT outcome FROM llm_calls WHERE id = %s", (reservation["id"],))
        assert cur.fetchone()[0] == "unknown"
        assert usage.spent_today(cur, user_id) == pytest.approx(reservation["reserved"])


# ── the system-wide ceiling ──────────────────────────────────────────────────

def test_the_global_cap_stops_a_user_who_is_under_their_own(_db, user_id, monkeypatch):
    """Per-user caps bound one account. Accounts are free, so only this bounds the bill."""
    monkeypatch.setattr(usage, "GLOBAL_DAILY_LIMIT_USD", usage.ceiling_cost("job_analysis") * 0.5)

    with pytest.raises(usage.SystemQuotaExceeded):
        usage.reserve(user_id, "job_analysis", "gpt-4o-mini")

    with _db.cursor() as cur:
        # the user was never charged for a call that did not happen: the global refusal has to
        # roll back its own reservation too
        assert usage.spent_today(cur, user_id) == 0


def test_one_account_cannot_be_dodged_by_making_another(_db, user_id, client, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_LIMIT_USD", usage.ceiling_cost("job_analysis") * 1.2)
    usage.reserve(user_id, "job_analysis", "gpt-4o-mini")      # the day's global room, used

    client.post("/api/auth/logout")
    client.post("/api/auth/signup", json={"username": "freshaccount", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "freshaccount", "password": "pw123456"})
    other_id = client.get("/api/auth/me").get_json()["id"]

    with pytest.raises(usage.SystemQuotaExceeded):
        usage.reserve(other_id, "job_analysis", "gpt-4o-mini")


def test_the_system_refusal_does_not_blame_the_user(_db, user_id, monkeypatch):
    monkeypatch.setattr(usage, "GLOBAL_DAILY_LIMIT_USD", 0.0001)
    with pytest.raises(usage.QuotaExceeded) as exc:      # still a QuotaExceeded for callers
        usage.reserve(user_id, "job_analysis", "gpt-4o-mini")
    assert "the service" in str(exc.value)
    assert "used today" not in str(exc.value)     # that message is about their own spending


def test_the_first_call_of_the_day_is_not_exempt(_db, user_id, monkeypatch):
    """The INSERT branch has no ON CONFLICT guard, so a cap below one call's ceiling would
    otherwise let exactly one call through every morning."""
    monkeypatch.setattr(usage, "GLOBAL_DAILY_LIMIT_USD", 0.0001)

    with _db.cursor() as cur:
        cur.execute("SELECT count(*) FROM llm_global_budget WHERE budget_date = current_date")
        assert cur.fetchone()[0] == 0, "this test only means something on an empty day"

    with pytest.raises(usage.SystemQuotaExceeded):
        usage.reserve(user_id, "job_analysis", "gpt-4o-mini")
