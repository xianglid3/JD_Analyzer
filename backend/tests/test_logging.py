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

    assert missing.missing == ["users.invented_column"]
    assert missing.reachable
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

    assert missing.missing == ["not_created_yet"]
    assert "not_created_yet" in caplog.text
    pathlib.Path(fake_schema).unlink()


def test_schema_check_is_quiet_when_the_database_matches(_db, caplog):
    import logging

    from db import check_schema

    with caplog.at_level(logging.ERROR):
        state = check_schema()
    assert state.ok and state.missing == []
    assert "missing" not in caplog.text


def test_readiness_fails_when_the_database_is_unreachable(client, monkeypatch):
    """The old checker returned None here, and `if missing:` read that as healthy — so an
    unreachable database would have passed readiness and taken traffic."""
    import db

    def refuse(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(db, "get_cursor", refuse)
    state = db.check_schema()

    assert not state.reachable and not state.ok


def test_readiness_reports_a_schema_that_is_behind(client, monkeypatch):
    import app as app_module
    from db import SchemaState

    monkeypatch.setattr(app_module, "check_schema",
                        lambda *a, **k: SchemaState(True, ["index tailoring_runs_one_active_per_job"]))
    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.get_json()["missing"] == ["index tailoring_runs_one_active_per_job"]
    # liveness is a different question and must not fail with it
    assert client.get("/api/health").status_code == 200
