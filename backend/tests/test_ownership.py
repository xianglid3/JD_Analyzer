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
