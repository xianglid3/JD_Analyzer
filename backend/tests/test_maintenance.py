"""The background sweeper.

`sweep_abandoned_runs` was already correct and already had a CLI command — but nothing ever
called it, so a run nobody revisited stayed `running` forever. These cover the scheduling
around it, and the guarantee that a failing sweep cannot take the process down.
"""

import threading

import pytest

from db import get_cursor
from services import maintenance
from services.tailoring_agent import HEARTBEAT_TIMEOUT, MAX_CLAIMS


@pytest.fixture
def stranded_run(_db):
    """A run no worker can rescue: its lease is gone and it has used every claim.

    Silence alone no longer qualifies — an expired lease just means the run is waiting to be
    claimed, and the sweep must leave that alone or it kills work in progress.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('sweeper', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO jobs (user_id, raw_description) VALUES (%s, 'jd') RETURNING id",
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status, heartbeat_at,
                                        claim_count, lease_expires_at)
            VALUES (%s, %s, 'gpt-4o-mini', 12, 'running',
                    now() - %s::interval - interval '1 minute',
                    %s, now() - interval '5 minutes')
            RETURNING id
            """,
            (user_id, job_id, HEARTBEAT_TIMEOUT, MAX_CLAIMS),
        )
        run_id = str(cur.fetchone()[0])

    # committed before the test runs: the sweep reads on its own connection, so an open
    # transaction here would hide the row from it
    yield run_id


def test_a_stranded_run_is_closed(stranded_run):
    assert maintenance.sweep_once() == [stranded_run]

    with get_cursor() as cur:
        cur.execute("SELECT status, error_code FROM tailoring_runs WHERE id = %s", (stranded_run,))
        assert cur.fetchone() == ("failed", "abandoned")


def test_a_healthy_run_is_left_alone(_db):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES ('sweeper2', 'x') RETURNING id"
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO jobs (user_id, raw_description) VALUES (%s, 'jd') RETURNING id",
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status, heartbeat_at)
            VALUES (%s, %s, 'gpt-4o-mini', 12, 'running', now())
            RETURNING id
            """,
            (user_id, job_id),
        )
        run_id = cur.fetchone()[0]

    assert str(run_id) not in maintenance.sweep_once()


def test_a_failing_sweep_is_swallowed(monkeypatch):
    """The sweeper runs on a daemon thread. An exception escaping it would kill the thread
    silently, and nothing would ever sweep again."""
    def explode(_cur):
        raise RuntimeError("database is gone")

    monkeypatch.setattr("services.tailoring_agent.sweep_abandoned_runs", explode)
    assert maintenance.sweep_once() == []


def test_the_sweeper_starts_at_most_once_per_process(monkeypatch):
    monkeypatch.setattr(maintenance, "_started", False)
    started = []
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: types_thread(started, kwargs))

    assert maintenance.start() is not None
    assert maintenance.start() is None          # second call is a no-op
    assert len(started) == 1


def types_thread(started, kwargs):
    class _Fake:
        def start(self):
            started.append(kwargs)
    return _Fake()


def test_the_sweeper_does_not_run_under_test_config(monkeypatch):
    monkeypatch.setattr(maintenance, "_started", False)

    class _App:
        config = {"TESTING": True}

    # the suite would otherwise get a background thread mutating rows mid-assertion
    assert maintenance.start(_App()) is None
