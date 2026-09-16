import logging
import os
import pathlib
import re
import threading

from config import env_int
import psycopg2
from psycopg2 import pool as psycopg2_pool
from dotenv import load_dotenv
from contextlib import contextmanager

load_dotenv()
logger = logging.getLogger(__name__)
SUPABASE_URL = os.environ["SUPABASE_URL"]

# A tailoring run touches the database ~25 times (a heartbeat and a tool write per step).
# Connecting per use meant ~25 TLS handshakes to the pooler for one run; these are borrowed
# instead. Threaded, because the tailoring worker runs off the request thread.
POOL_MIN = env_int("DB_POOL_MIN", 1)
POOL_MAX = env_int("DB_POOL_MAX", 10)

# A query with no ceiling holds its pooled connection for as long as it runs, so a handful of
# stuck ones can starve every other request of a connection. Nothing here should take
# anywhere near this long; it only fires when something is already wrong, and it turns a
# silent hang into a normal error the caller can report.
STATEMENT_TIMEOUT_MS = env_int("DB_STATEMENT_TIMEOUT_MS", 15000)

_pool = None
_pool_lock = threading.Lock()


class _TimeoutPool(psycopg2_pool.ThreadedConnectionPool):
    """A pool whose connections refuse to run a single statement forever.

    Applied once when the connection is opened, not per borrow. Doing it per borrow means
    a statement — and therefore an open transaction — on every `get_cursor`, which changes
    when locks are taken for every caller in the app. Doing it here costs one round trip per
    *connection*, and the setting then lasts that connection's whole life.
    """

    def _connect(self, key=None):
        connection = super()._connect(key)
        try:
            connection.autocommit = True
            with connection.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
        finally:
            connection.autocommit = False
        return connection


def get_pool():
    """Created on first use, not at import: tests and CLI commands import db without a
    database, and a pool built at import time would fail them at collection."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _TimeoutPool(POOL_MIN, POOL_MAX, dsn=SUPABASE_URL)
    return _pool


def reset_pool():
    """Drop every pooled connection. For tests, and for a worker that forked after the pool
    existed — a connection shared across a fork is a corrupt connection."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                logger.warning("could not close the connection pool cleanly")
            _pool = None


#connect dat db
def get_connection():
    return psycopg2.connect(SUPABASE_URL)


#??
@contextmanager
def get_cursor(commit=False):
    pool = get_pool()
    connection = pool.getconn()
    broken = False
    cur = connection.cursor()
    try:
        yield cur
        if commit:
            connection.commit()
    except Exception as exc:
        # a connection that died mid-statement can't be rolled back or reused
        broken = isinstance(exc, psycopg2.OperationalError) or connection.closed
        if not broken:
            connection.rollback()
        raise
    finally:
        if not broken:
            cur.close()
            # never hand back a connection mid-transaction: the next borrower would inherit it
            if connection.status != psycopg2.extensions.STATUS_READY:
                connection.rollback()
        pool.putconn(connection, close=broken)


CONSTRAINT_LINE = re.compile(
    r"^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b", re.I
)


def expected_schema(schema_path=None):
    """{table: {columns}} as schema.sql defines them."""
    path = pathlib.Path(schema_path or pathlib.Path(__file__).with_name("schema.sql"))
    tables = {}
    for match in re.finditer(
        r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\)\s*;",
        path.read_text(), re.S,
    ):
        table, body = match.group(1), match.group(2)
        columns = set()
        for line in body.splitlines():
            line = re.sub(r"--.*", "", line).strip()
            if not line or CONSTRAINT_LINE.match(line):
                continue
            name = line.split()[0].strip('",')
            if name.isidentifier():
                columns.add(name)
        tables[table] = columns
    return tables


def expected_indexes(schema_path=None):
    """Index names schema.sql creates.

    Columns are not the whole schema. A partial unique index is the only thing standing
    between one active tailoring run per job and two, and a column-only check cannot see it —
    so the artifact that declares it is also what verifies it.
    """
    path = pathlib.Path(schema_path or pathlib.Path(__file__).with_name("schema.sql"))
    return {
        match.group(1)
        for match in re.finditer(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF NOT EXISTS\s+)?(\w+)\s+ON",
            path.read_text(), re.I,
        )
    }


class SchemaState:
    """What the live database looks like next to `schema.sql`.

    Three states, not two. `reachable=False` is emphatically not "nothing missing" — the old
    version returned None there, and a caller doing `if missing:` would have read an
    unreachable database as a healthy one.
    """

    def __init__(self, reachable, missing=(), error=None):
        self.reachable = reachable
        self.missing = list(missing)
        self.error = error

    @property
    def ok(self):
        return self.reachable and not self.missing

    def __bool__(self):
        return self.ok


def check_schema(schema_path=None):
    """Compare the live database with what this code expects.

    The database is hand-edited, so code can ship expecting a table, column or index nobody
    created. That surfaces as a 500 mid-request, on whichever action touches it first;
    reading the difference at boot turns it into one line naming what to add.
    """
    expected = expected_schema(schema_path)
    wanted_indexes = expected_indexes(schema_path)

    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
            )
            present = {}
            for table, column in cur.fetchall():
                present.setdefault(table, set()).add(column)
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            present_indexes = {row[0] for row in cur.fetchall()}
    except Exception as exc:
        # The reason matters more than the fact. "unreachable" covers a wrong host, a paused
        # project, a rejected password and a firewall, and without the driver's own message
        # the only way to tell them apart is guesswork. It names no password: psycopg2 reports
        # the host and the failure, not the credentials.
        logger.warning("could not reach the database at startup: %s", exc)
        return SchemaState(reachable=False, error=str(exc))

    missing = []
    for table, columns in expected.items():
        if table not in present:
            missing.append(table)
            continue
        missing += [f"{table}.{column}" for column in sorted(columns - present[table])]
    missing += [f"index {name}" for name in sorted(wanted_indexes - present_indexes)]

    if missing:
        logger.error("database is behind this code — missing: %s", ", ".join(sorted(missing)))
    return SchemaState(reachable=True, missing=sorted(missing))
