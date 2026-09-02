"""Request ids and the log lines that use them."""

import logging


def test_every_api_request_gets_an_id(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get("/api/health")

    line = next(r for r in caplog.records if "/api/health" in r.getMessage())
    assert len(line.request_id) == 8
    assert "GET /api/health 200" in line.getMessage()


def test_two_requests_get_different_ids(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get("/api/health")
        client.get("/api/health")

    ids = {r.request_id for r in caplog.records if "/api/health" in r.getMessage()}
    assert len(ids) == 2


def test_the_line_carries_the_user_once_authenticated(client, caplog):
    client.post("/api/auth/signup", json={"username": "loguser", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "loguser", "password": "pw123456"})

    with caplog.at_level(logging.INFO):
        client.get("/api/auth/me")

    line = next(r for r in caplog.records if "/api/auth/me" in r.getMessage())
    assert "user=-" not in line.getMessage()


def test_cost_is_derived_from_token_counts():
    from services.openai_services import usd

    assert usd(1_000_000, 0) == 0.15
    assert usd(0, 1_000_000) == 0.60
    assert usd(0, 0) == 0


def test_schema_check_reports_missing_columns(_db, caplog):
    """The tables existed; the columns the code needed did not. Both are the same failure."""
    import logging
    import pathlib
    import tempfile

    from db import check_schema

    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write("CREATE TABLE users (\n  id uuid,\n  username text,\n  invented_column text\n);\n")
        fake_schema = handle.name

    with caplog.at_level(logging.ERROR):
        missing = check_schema(fake_schema)

    assert missing == ["users.invented_column"]
    pathlib.Path(fake_schema).unlink()


def test_schema_check_ignores_constraint_lines(_db):
    """A CHECK or PRIMARY KEY line is not a column."""
    import pathlib
    import tempfile

    from db import expected_schema

    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write(
            "CREATE TABLE users (\n  id uuid,\n  PRIMARY KEY (id),\n"
            "  CHECK (id IS NOT NULL),\n  UNIQUE (id)\n);\n"
        )
        fake_schema = handle.name

    assert expected_schema(fake_schema) == {"users": {"id"}}
    pathlib.Path(fake_schema).unlink()


def test_schema_check_reports_missing_tables(_db, caplog):
    """Four separate 'relation does not exist' 500s came from the code expecting tables the
    hand-edited database didn't have yet."""
    import logging
    import pathlib
    import tempfile

    from db import check_schema

    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
        handle.write("CREATE TABLE users (id uuid);\nCREATE TABLE not_created_yet (id uuid);\n")
        fake_schema = handle.name

    with caplog.at_level(logging.ERROR):
        missing = check_schema(fake_schema)

    assert missing == ["not_created_yet"]
    assert "not_created_yet" in caplog.text
    pathlib.Path(fake_schema).unlink()


def test_schema_check_is_quiet_when_the_database_matches(_db, caplog):
    import logging

    from db import check_schema

    with caplog.at_level(logging.ERROR):
        assert check_schema() == []
    assert "missing" not in caplog.text
