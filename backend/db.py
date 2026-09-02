import logging
import os
import pathlib
import re

import psycopg2
from dotenv import load_dotenv
from contextlib import contextmanager

load_dotenv()
logger = logging.getLogger(__name__)
SUPABASE_URL = os.environ["SUPABASE_URL"]

#connect dat db
def get_connection():
    return psycopg2.connect(SUPABASE_URL)


#??
@contextmanager
def get_cursor(commit=False):
    connection = get_connection()
    cur = connection.cursor()
    try:
        yield cur
        if commit:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        connection.close()


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


def check_schema(schema_path=None):
    """Warn when the live database is behind what the code expects.

    The database is hand-edited, so code can ship expecting a table or column nobody
    created. That surfaces as a 500 mid-request, on whichever action touches it first;
    reading the difference at boot turns it into one line naming what to add.
    """
    expected = expected_schema(schema_path)

    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
            )
            present = {}
            for table, column in cur.fetchall():
                present.setdefault(table, set()).add(column)
    except Exception:
        logger.warning("could not check the schema — database unreachable at startup")
        return None

    missing = []
    for table, columns in expected.items():
        if table not in present:
            missing.append(table)
            continue
        missing += [f"{table}.{column}" for column in sorted(columns - present[table])]

    if missing:
        logger.error("database is behind this code — missing: %s", ", ".join(sorted(missing)))
    return sorted(missing)
