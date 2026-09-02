import json

import pytest


USER = {"username": "fieldvalidationuser", "password": "pw123456"}


def login(client):
    user = client.post("/api/auth/signup", json=USER).get_json()
    assert client.post("/api/auth/login", json=USER).status_code == 200
    return user


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"status": []}, "invalid status"),
        ({"notes": {}}, "notes must be text or null"),
        ({"notes": "x" * 5001}, "notes must be 5000 characters or fewer"),
        ({"deadline": 123}, "deadline must be YYYY-MM-DD or null"),
        ({"deadline": "2026-02-30"}, "deadline must be YYYY-MM-DD or null"),
        ({"deadline": "20260824"}, "deadline must be YYYY-MM-DD or null"),
        ({"source_url": "javascript:alert(1)"}, "source_url must be a valid HTTP or HTTPS URL"),
    ],
)
def test_job_update_rejects_invalid_field_values(client, insert_job, payload, message):
    user = login(client)
    job_id = insert_job(user["id"])

    response = client.patch(f"/api/jobs/{job_id}", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_job_update_accepts_valid_notes_and_deadline(client, insert_job):
    user = login(client)
    job_id = insert_job(user["id"])

    response = client.patch(
        f"/api/jobs/{job_id}",
        json={"notes": "Prepare portfolio", "deadline": "2026-08-24"},
    )

    assert response.status_code == 200
    job = client.get(f"/api/jobs/{job_id}").get_json()
    assert job["notes"] == "Prepare portfolio"
    assert job["deadline"] == "2026-08-24"


def test_job_update_normalizes_and_removes_source_url(client, insert_job):
    user = login(client)
    job_id = insert_job(user["id"])

    response = client.patch(
        f"/api/jobs/{job_id}",
        json={"source_url": " HTTPS://Example.COM:443/jobs/42#apply "},
    )

    assert response.status_code == 200
    assert client.get(f"/api/jobs/{job_id}").get_json()["source_url"] == "https://example.com/jobs/42"

    assert client.patch(f"/api/jobs/{job_id}", json={"source_url": None}).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").get_json()["source_url"] is None


@pytest.mark.parametrize(
    "field",
    ["education", "work_experience", "projects", "skills", "certificates"],
)
def test_resume_fields_must_be_arrays(client, field):
    login(client)

    response = client.put("/api/resume", json={field: {}})

    assert response.status_code == 400
    assert response.get_json() == {"error": f"{field} must be an array"}


@pytest.mark.parametrize(
    ("skills", "message"),
    [
        (["Python", 3], "each skill must be non-empty text"),
        (["Python", "  "], "each skill must be non-empty text"),
        (["x" * 101], "each skill must be 100 characters or fewer"),
        (["skill"] * 101, "skills must contain 100 items or fewer"),
    ],
)
def test_resume_rejects_invalid_skills(client, skills, message):
    login(client)

    response = client.put("/api/resume", json={"skills": skills})

    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_resume_trims_and_deduplicates_skills(client):
    login(client)

    response = client.put(
        "/api/resume",
        json={"skills": [" Python ", "python", "SQL"]},
    )

    assert response.status_code == 200
    assert client.get("/api/resume").get_json()["skills"] == ["Python", "SQL"]


@pytest.mark.parametrize(
    ("resume_text", "message"),
    [
        ([], "resume_text must be text or null"),
        ("x" * 20001, "resume_text must be 20000 characters or fewer"),
    ],
)
def test_resume_rejects_invalid_saved_text(client, resume_text, message):
    login(client)

    response = client.put("/api/resume", json={"resume_text": resume_text})

    assert response.status_code == 400
    assert response.get_json() == {"error": message}


def test_resume_save_recomputes_multiple_jobs_with_display_names(client, _db):
    user = login(client)
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills)
            VALUES
                (%s, %s, 'Frontend role', %s),
                (%s, %s, 'Backend role', %s)
            """,
            (
                user["id"], "x" * 60, json.dumps(["JavaScript", "REST API"]),
                user["id"], "x" * 60, json.dumps(["Python", "Docker"]),
            ),
        )

    response = client.put("/api/resume", json={"skills": ["js", "python"]})

    assert response.status_code == 200
    with _db.cursor() as cur:
        cur.execute(
            "SELECT title, match_score, match_detail FROM jobs WHERE user_id = %s ORDER BY title",
            (user["id"],),
        )
        rows = cur.fetchall()

    # match_detail carries the whole explanation now — states, evidence, both scores — so
    # this checks what the test was always about: recomputation ran and kept JD display casing
    assert [(title, float(score)) for title, score, _ in rows] == [
        ("Backend role", 50.0),
        ("Frontend role", 50.0),
    ]
    assert [(detail["matched"], detail["missing"]) for _, _, detail in rows] == [
        (["Python"], ["Docker"]),
        (["JavaScript"], ["REST API"]),
    ]
