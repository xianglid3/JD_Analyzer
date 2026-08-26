from uuid import uuid4

from services.openai_services import JobExtraction


USER = {"username": "idempotencyuser", "password": "pw123456"}
DESCRIPTION = "Backend engineer role requiring Python, PostgreSQL, and Docker."


def login(client):
    client.post("/api/auth/signup", json=USER)
    assert client.post("/api/auth/login", json=USER).status_code == 200


def extracted_job():
    return JobExtraction(
        title="Backend Engineer",
        summary="Build backend services.",
        no_bs_translation="Write and maintain APIs.",
        skills=["Python", "PostgreSQL", "Docker"],
        company_name="Acme",
        location="New York",
        work_type="hybrid",
    )


def test_same_key_replays_original_job_without_second_analysis(client, monkeypatch):
    login(client)
    calls = []

    def analyze(description):
        calls.append(description)
        return extracted_job()

    monkeypatch.setattr("routes.jobs.analyze_job_description", analyze)
    headers = {"Idempotency-Key": str(uuid4())}

    first = client.post("/api/jobs", json={"description": DESCRIPTION}, headers=headers)
    replay = client.post("/api/jobs", json={"description": DESCRIPTION}, headers=headers)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.get_json()["id"] == first.get_json()["id"]
    assert replay.get_json()["replayed"] is True
    assert len(calls) == 1


def test_same_key_rejects_a_different_description(client, monkeypatch):
    login(client)
    calls = []

    def analyze(description):
        calls.append(description)
        return extracted_job()

    monkeypatch.setattr("routes.jobs.analyze_job_description", analyze)
    headers = {"Idempotency-Key": str(uuid4())}

    first = client.post("/api/jobs", json={"description": DESCRIPTION}, headers=headers)
    conflict = client.post(
        "/api/jobs",
        json={"description": DESCRIPTION + " Different payload."},
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "different description" in conflict.get_json()["error"]
    assert len(calls) == 1


def test_failed_analysis_releases_key_for_retry(client, monkeypatch):
    login(client)
    calls = 0

    def analyze(_description):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary test failure")
        return extracted_job()

    monkeypatch.setattr("routes.jobs.analyze_job_description", analyze)
    headers = {"Idempotency-Key": str(uuid4())}

    failed = client.post("/api/jobs", json={"description": DESCRIPTION}, headers=headers)
    retried = client.post("/api/jobs", json={"description": DESCRIPTION}, headers=headers)

    assert failed.status_code == 500
    assert retried.status_code == 201
    assert calls == 2


def test_create_job_requires_a_uuid_idempotency_key(client, monkeypatch):
    login(client)
    analyze_called = False

    def analyze(_description):
        nonlocal analyze_called
        analyze_called = True
        return extracted_job()

    monkeypatch.setattr("routes.jobs.analyze_job_description", analyze)

    response = client.post("/api/jobs", json={"description": DESCRIPTION})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Valid Idempotency-Key required"}
    assert analyze_called is False


def test_new_analysis_sweeps_expired_completed_keys(client, monkeypatch, _db):
    login(client)
    monkeypatch.setattr("routes.jobs.analyze_job_description", lambda _description: extracted_job())
    expired_key = str(uuid4())
    current_key = str(uuid4())

    assert client.post(
        "/api/jobs",
        json={"description": DESCRIPTION},
        headers={"Idempotency-Key": expired_key},
    ).status_code == 201

    with _db.cursor() as cur:
        cur.execute(
            """
            UPDATE idempotency_requests
            SET created_at = now() - interval '8 days'
            WHERE idempotency_key = %s
            """,
            (expired_key,),
        )

    assert client.post(
        "/api/jobs",
        json={"description": DESCRIPTION},
        headers={"Idempotency-Key": current_key},
    ).status_code == 201

    with _db.cursor() as cur:
        cur.execute(
            "SELECT idempotency_key FROM idempotency_requests ORDER BY created_at"
        )
        keys = [row[0] for row in cur.fetchall()]

    assert expired_key not in keys
    assert current_key in keys
