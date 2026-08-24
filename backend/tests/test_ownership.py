"""Integration tests: ownership is enforced in SQL, not in the UI.
These run against a real throwaway Postgres (see conftest); they skip when none is reachable."""

A = {"username": "alice", "password": "pw123456"}
B = {"username": "bob", "password": "pw123456"}


def test_user_b_cannot_read_user_a_job(client, insert_job):
    # user A signs up; give A a job directly
    a = client.post("/api/auth/signup", json=A).get_json()
    job_id = insert_job(a["id"])

    # user B signs up + logs in — the client's cookie jar now belongs to B
    client.post("/api/auth/signup", json=B)
    client.post("/api/auth/login", json=B)

    # B cannot read A's job — 404 (not 403), because the WHERE clause just returns no row
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    # ...and it never shows up in B's list
    assert client.get("/api/jobs").get_json()["jobs"] == []


def test_user_b_cannot_delete_user_a_job(client, insert_job):
    a = client.post("/api/auth/signup", json=A).get_json()
    job_id = insert_job(a["id"])

    client.post("/api/auth/signup", json=B)
    client.post("/api/auth/login", json=B)

    # B's delete affects no rows → 404
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404

    # and the job is still there for A
    client.post("/api/auth/login", json=A)
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_user_b_cannot_patch_user_a_job(client, insert_job):
    a = client.post("/api/auth/signup", json=A).get_json()
    job_id = insert_job(a["id"])

    client.post("/api/auth/signup", json=B)
    client.post("/api/auth/login", json=B)

    assert client.patch(
        f"/api/jobs/{job_id}",
        json={"status": "applied"},
    ).status_code == 404

    client.post("/api/auth/login", json=A)
    assert client.get(f"/api/jobs/{job_id}").get_json()["status"] == "saved"


def test_job_list_paginates_with_metadata(client, insert_job):
    user = client.post("/api/auth/signup", json=A).get_json()
    client.post("/api/auth/login", json=A)

    for number in range(25):
        insert_job(user["id"], description=f"job {number} " + "x" * 60)

    first = client.get("/api/jobs?page=1").get_json()
    second = client.get("/api/jobs?page=2").get_json()

    assert len(first["jobs"]) == 20
    assert len(second["jobs"]) == 5
    assert first["total"] == 25
    assert first["total_pages"] == 2
    assert first["per_page"] == 20
    assert {job["id"] for job in first["jobs"]}.isdisjoint(
        {job["id"] for job in second["jobs"]}
    )


def test_job_list_rejects_invalid_page(client):
    client.post("/api/auth/signup", json=A)
    client.post("/api/auth/login", json=A)

    assert client.get("/api/jobs?page=0").status_code == 400
    assert client.get("/api/jobs?page=nope").status_code == 400


def test_job_list_filters_searches_and_sorts_before_paginating(client, insert_job):
    user = client.post("/api/auth/signup", json=A).get_json()
    client.post("/api/auth/login", json=A)

    insert_job(
        user["id"], title="Backend Engineer", company_name="Acme",
        status="applied", match_score=75,
    )
    insert_job(
        user["id"], title="Frontend Engineer", company_name="Acme",
        status="applied", match_score=25,
    )
    insert_job(
        user["id"], title="Designer", company_name="Other Co",
        status="saved", match_score=90,
    )

    response = client.get(
        "/api/jobs?search=acme&status=applied&sort=match_score&direction=asc"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["total"] == 2
    assert [job["title"] for job in data["jobs"]] == [
        "Frontend Engineer",
        "Backend Engineer",
    ]
