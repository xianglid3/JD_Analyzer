from uuid import uuid4

from services.openai_services import JobExtraction


A = {"username": "draftownera", "password": "pw123456"}
B = {"username": "draftownerb", "password": "pw123456"}
DESCRIPTION = "Backend engineer role requiring Python, PostgreSQL, and Docker."


def extracted_job():
    return JobExtraction(
        title="Backend Engineer",
        summary="Build reliable backend services.",
        no_bs_translation="Own APIs and production systems.",
        skills=["Python", "PostgreSQL", "Docker"],
        company_name="Acme",
        location="New York",
        work_type="hybrid",
    )


def analyze_draft(client, monkeypatch, credentials=A):
    client.post("/api/auth/signup", json=credentials)
    assert client.post("/api/auth/login", json=credentials).status_code == 200
    monkeypatch.setattr("routes.jobs.analyze_job_description", lambda _text: extracted_job())
    return client.post(
        "/api/jobs/drafts",
        json={"description": DESCRIPTION, "source_url": "https://example.com/jobs/42"},
        headers={"Idempotency-Key": str(uuid4())},
    )


def test_analysis_creates_only_a_draft_until_confirmation(client, monkeypatch):
    draft_response = analyze_draft(client, monkeypatch)

    assert draft_response.status_code == 201
    draft = draft_response.get_json()
    assert draft["title"] == "Backend Engineer"
    assert client.get("/api/jobs").get_json()["total"] == 0

    confirmed = client.post(
        f"/api/jobs/drafts/{draft['id']}/confirm",
        json={
            "title": "Senior Backend Engineer",
            "company_name": "Acme Corp",
            "location": "Boston",
            "work_type": "remote",
            "source_url": "https://example.com/jobs/42#apply",
            "status": "applied",
            "deadline": "2026-09-30",
        },
    )

    assert confirmed.status_code == 201
    job_id = confirmed.get_json()["id"]
    job = client.get(f"/api/jobs/{job_id}").get_json()
    assert job["title"] == "Senior Backend Engineer"
    assert job["company_name"] == "Acme Corp"
    assert job["work_type"] == "remote"
    assert job["status"] == "applied"
    assert job["deadline"] == "2026-09-30"
    assert job["source_url"] == "https://example.com/jobs/42"

    replay = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={})
    assert replay.status_code == 200
    assert replay.get_json() == {"id": job_id, "replayed": True}
    assert client.get("/api/jobs").get_json()["total"] == 1


def test_drafts_are_ownership_scoped(client, monkeypatch):
    draft = analyze_draft(client, monkeypatch).get_json()

    client.post("/api/auth/signup", json=B)
    client.post("/api/auth/login", json=B)

    assert client.get(f"/api/jobs/drafts/{draft['id']}").status_code == 404
    assert client.delete(f"/api/jobs/drafts/{draft['id']}").status_code == 404
    assert client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={}).status_code == 404


def test_expired_draft_cannot_be_read_or_confirmed(client, monkeypatch, _db):
    draft = analyze_draft(client, monkeypatch).get_json()
    with _db.cursor() as cur:
        cur.execute(
            "UPDATE job_analysis_drafts SET expires_at = now() - interval '1 minute' WHERE id = %s",
            (draft["id"],),
        )

    assert client.get(f"/api/jobs/drafts/{draft['id']}").status_code == 410
    assert client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={}).status_code == 410


def test_cancelling_a_draft_creates_no_job(client, monkeypatch):
    draft = analyze_draft(client, monkeypatch).get_json()

    assert client.delete(f"/api/jobs/drafts/{draft['id']}").status_code == 200
    assert client.get(f"/api/jobs/drafts/{draft['id']}").status_code == 404
    assert client.get("/api/jobs").get_json()["total"] == 0
