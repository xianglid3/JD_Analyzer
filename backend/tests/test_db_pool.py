"""The connection pool (BUG-101).

A tailoring run borrows a connection ~25 times. These check that borrowing returns the same
connections rather than dialling out each time, and — the part that actually matters — that a
connection is never handed back to the next borrower in a broken or mid-transaction state.
"""

import psycopg2
import pytest

import db


@pytest.fixture(autouse=True)
def fresh_pool(_db):
    db.reset_pool()
    yield
    db.reset_pool()


def test_connections_are_reused_rather_than_reopened():
    seen = set()
    for _ in range(5):
        with db.get_cursor() as cur:
            cur.execute("SELECT 1")
            seen.add(id(cur.connection))

    assert len(seen) == 1          # five borrows, one underlying connection


def test_a_failed_statement_does_not_poison_the_next_borrower():
    with pytest.raises(psycopg2.errors.UndefinedTable):
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM a_table_that_is_not_there")

    # the rollback happened on the way out, so this connection is usable again
    with db.get_cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_an_uncommitted_write_is_not_inherited_by_the_next_borrower():
    with db.get_cursor() as cur:          # commit=False: the INSERT should never land
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('pooltest', 'x')")

    with db.get_cursor() as cur:
        # This borrow is already in a transaction of its own — get_cursor sets the statement
        # timeout on the way in — so the check is that the transaction is a *fresh* one
        # carrying none of the previous borrower's work, not that the connection is idle.
        cur.execute("SELECT count(*) FROM users WHERE username = 'pooltest'")
        assert cur.fetchone()[0] == 0


def test_commit_true_still_commits():
    with db.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('poolcommit', 'x')")

    with db.get_cursor() as cur:
        cur.execute("SELECT count(*) FROM users WHERE username = 'poolcommit'")
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM users WHERE username = 'poolcommit'")
        cur.connection.commit()


def test_reset_pool_lets_a_new_pool_be_built():
    with db.get_cursor() as cur:
        cur.execute("SELECT 1")
    db.reset_pool()

    with db.get_cursor() as cur:        # rebuilt on demand rather than raising
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_a_runaway_query_is_killed_rather_than_holding_its_connection():
    """Without a ceiling, one stuck query keeps its pooled connection forever, and enough of
    them starve every other request. The timeout turns a hang into an ordinary error."""
    import psycopg2

    with pytest.raises(psycopg2.errors.QueryCanceled):
        with db.get_cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 100")
            cur.execute("SELECT pg_sleep(3)")

    # and the pool is still usable afterwards — the connection came back, not leaked
    with db.get_cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1


def test_the_timeout_is_applied_to_every_borrowed_cursor():
    from db import STATEMENT_TIMEOUT_MS

    with db.get_cursor() as cur:
        cur.execute("SELECT setting::int FROM pg_settings WHERE name = 'statement_timeout'")
        assert cur.fetchone()[0] == STATEMENT_TIMEOUT_MS
