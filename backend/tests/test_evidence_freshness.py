"""Bullet ids surviving edits, and evidence going stale when the resume changes."""

import pytest

from services.openai_services import ResumeEntryExtraction, ResumeStructure


USER = {"username": "freshuser", "password": "pw123456"}
RESUME_TEXT = "Backend intern at Acme. Shipped an ingestion pipeline in Python. " * 3
NEW_RESUME_TEXT = "Frontend intern at Globex. Built dashboards in React. " * 3


@pytest.fixture(autouse=True)
def fake_extraction(monkeypatch):
    """The route is exercised for its hashing, not the model's output."""
    def extract(text, **_kwargs):
        return ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", bullets=["Shipped the pipeline"]),
        ], skills=["Python"])

    monkeypatch.setattr("routes.resume.analyze_resume_structure", extract)


def login(client):
    client.post("/api/auth/signup", json=USER)
    client.post("/api/auth/login", json=USER)
    return client.get("/api/auth/me").get_json()["id"]


def entry(*bullets, org="Acme"):
    return {"kind": "experience", "organization": org, "title": "Intern", "bullets": list(bullets)}


def bullets_by_text(client):
    entries = client.get("/api/resume/evidence").get_json()["entries"]
    return {b["text"]: b["id"] for e in entries for b in e["bullets"]}


# ── ids survive an edit ──────────────────────────────────────────────────────

def test_editing_a_bullet_keeps_its_id(client):
    login(client)
    client.put("/api/resume/evidence", json={"entries": [entry("Shipped the pipeline")]})
    original = bullets_by_text(client)["Shipped the pipeline"]

    client.put("/api/resume/evidence", json={
        "entries": [entry({"id": original, "text": "Shipped the ingestion pipeline in Python"})],
    })

    after = bullets_by_text(client)
    assert after == {"Shipped the ingestion pipeline in Python": original}


def test_an_id_from_another_user_is_ignored(client):
    login(client)
    client.put("/api/resume/evidence", json={"entries": [entry("Mine")]})
    stolen = bullets_by_text(client)["Mine"]
    client.post("/api/auth/logout")

    client.post("/api/auth/signup", json={"username": "freshother", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "freshother", "password": "pw123456"})
    client.put("/api/resume/evidence", json={"entries": [entry({"id": stolen, "text": "Theirs"})]})

    assert bullets_by_text(client)["Theirs"] != stolen


def test_the_same_id_twice_is_rejected(client):
    login(client)
    client.put("/api/resume/evidence", json={"entries": [entry("One")]})
    bullet_id = bullets_by_text(client)["One"]

    response = client.put("/api/resume/evidence", json={
        "entries": [entry({"id": bullet_id, "text": "One"}, {"id": bullet_id, "text": "Two"})],
    })

    assert response.status_code == 400
    assert "twice" in response.get_json()["error"]


def test_unchanged_text_still_matches_by_hash(client):
    """Re-extraction sends no ids, so the hash is what keeps them alive."""
    login(client)
    client.put("/api/resume/evidence", json={"entries": [entry("Shipped the pipeline")]})
    original = bullets_by_text(client)["Shipped the pipeline"]

    client.put("/api/resume/evidence", json={"entries": [entry("Shipped the pipeline", "And tests")]})

    assert bullets_by_text(client)["Shipped the pipeline"] == original


# ── staleness ────────────────────────────────────────────────────────────────

def save_resume(client, text):
    return client.put("/api/resume", json={"skills": ["Python"], "resume_text": text})


def test_evidence_is_fresh_when_it_matches_the_resume(client, monkeypatch):
    login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT})

    client.put("/api/resume/evidence", json={
        "entries": [entry("Shipped the pipeline")],
        "source_hash": structure.get_json()["source_hash"],
    })

    assert client.get("/api/resume/evidence").get_json()["stale"] is False


def test_replacing_the_resume_makes_the_evidence_stale(client):
    login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT})
    client.put("/api/resume/evidence", json={
        "entries": [entry("Shipped the pipeline")],
        "source_hash": structure.get_json()["source_hash"],
    })

    save_resume(client, NEW_RESUME_TEXT)

    assert client.get("/api/resume/evidence").get_json()["stale"] is True


def test_stale_evidence_is_left_out_of_match_scoring(client, _db, insert_job):
    """Otherwise a job keeps scoring against a resume the user replaced."""
    user_id = login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT})
    client.put("/api/resume/evidence", json={
        "entries": [entry("Built dashboards with Tailwind")],
        "source_hash": structure.get_json()["source_hash"],
    })

    from services.skill_evidence import evaluate_requirements, load_evidence_bullets
    with _db.cursor() as cur:
        fresh = evaluate_requirements(["CSS"], load_evidence_bullets(cur, user_id))
    assert fresh["requirements"][0]["state"] == "INFERRED"

    save_resume(client, NEW_RESUME_TEXT)

    with _db.cursor() as cur:
        stale = evaluate_requirements(["CSS"], load_evidence_bullets(cur, user_id))
    assert stale["requirements"][0]["state"] == "NONE"


def test_tailoring_refuses_to_run_on_stale_evidence(client, insert_job):
    user_id = login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT})
    client.put("/api/resume/evidence", json={
        "entries": [entry("Shipped the pipeline")],
        "source_hash": structure.get_json()["source_hash"],
    })
    job_id = insert_job(user_id, title="Platform Engineer")

    save_resume(client, NEW_RESUME_TEXT)
    response = client.post(f"/api/jobs/{job_id}/tailor")

    assert response.status_code == 409
    body = response.get_json()
    # the reason code sends the client to the review gate instead of silently trusting a
    # fresh model extraction.
    assert body["reason"] == "needs_confirmation"
    assert "changed" in body["error"]


def test_re_extracting_clears_the_stale_flag(client):
    login(client)
    save_resume(client, RESUME_TEXT)
    first = client.post("/api/resume/structure", json={"text": RESUME_TEXT}).get_json()
    client.put("/api/resume/evidence", json={"entries": [entry("Shipped it")], "source_hash": first["source_hash"]})
    save_resume(client, NEW_RESUME_TEXT)

    second = client.post("/api/resume/structure", json={}).get_json()
    client.put("/api/resume/evidence", json={"entries": [entry("Built dashboards")], "source_hash": second["source_hash"]})

    assert client.get("/api/resume/evidence").get_json()["stale"] is False


def test_evidence_without_a_resume_is_never_stale(client):
    """Hand-written evidence has no extraction behind it to be out of date with."""
    login(client)
    client.put("/api/resume/evidence", json={"entries": [entry("Typed by hand")]})

    assert client.get("/api/resume/evidence").get_json()["stale"] is False


def test_editing_stale_evidence_does_not_make_it_fresh(client):
    """Otherwise the warning is cleared by opening the editor and pressing save."""
    login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT}).get_json()
    client.put("/api/resume/evidence", json={
        "entries": [entry("Shipped the pipeline")],
        "source_hash": structure["source_hash"],
    })
    save_resume(client, NEW_RESUME_TEXT)
    assert client.get("/api/resume/evidence").get_json()["stale"] is True

    client.put("/api/resume/evidence", json={"entries": [entry("Shipped the pipeline, edited")]})

    assert client.get("/api/resume/evidence").get_json()["stale"] is True


def test_the_editor_is_given_the_stamp_to_send_back(client):
    login(client)
    save_resume(client, RESUME_TEXT)
    structure = client.post("/api/resume/structure", json={"text": RESUME_TEXT}).get_json()
    client.put("/api/resume/evidence", json={
        "entries": [entry("Shipped the pipeline")],
        "source_hash": structure["source_hash"],
    })

    assert client.get("/api/resume/evidence").get_json()["source_hash"] == structure["source_hash"]


def test_hand_written_evidence_is_stamped_once(client):
    """Never-extracted evidence still needs a stamp, or it would read as stale forever."""
    login(client)
    save_resume(client, RESUME_TEXT)

    client.put("/api/resume/evidence", json={"entries": [entry("Typed by hand")]})

    assert client.get("/api/resume/evidence").get_json()["stale"] is False
