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
