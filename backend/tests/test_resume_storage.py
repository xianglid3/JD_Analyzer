import hashlib
import json
from io import BytesIO


USER = {"username": "resumestorageuser", "password": "pw123456"}


def login(client):
    user = client.post("/api/auth/signup", json=USER).get_json()
    assert client.post("/api/auth/login", json=USER).status_code == 200
    return user


def multipart_resume(file_bytes, filename="resume.txt", skills=None):
    payload = {
        "skills": skills or ["Python"],
        "resume_text": file_bytes.decode("utf-8"),
    }
    return {
        "resume": json.dumps(payload),
        "file": (BytesIO(file_bytes), filename),
    }


def test_resume_save_uploads_private_source_and_persists_metadata(client, monkeypatch, _db):
    user = login(client)
    uploads = []
    monkeypatch.setattr(
        "routes.resume.upload_resume_file",
        lambda path, content, content_type: uploads.append((path, content, content_type)),
    )
    monkeypatch.setattr("routes.resume.delete_resume_file", lambda _path: None)
    content = ("Backend engineer with Python and PostgreSQL experience. " * 3).encode()

    response = client.put(
        "/api/resume",
        data=multipart_resume(content),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["file_saved"] is True
    assert len(uploads) == 1
    storage_path, uploaded_content, content_type = uploads[0]
    assert storage_path.startswith(f"{user['id']}/")
    assert storage_path.endswith(".txt")
    assert uploaded_content == content
    assert content_type == "text/plain"

    resume = client.get("/api/resume").get_json()
    assert resume["resume_text"] == content.decode().strip()
    assert resume["source_file"]["filename"] == "resume.txt"
    assert resume["source_file"]["size_bytes"] == len(content)

    with _db.cursor() as cur:
        cur.execute(
            "SELECT storage_path, file_sha256 FROM resumes WHERE user_id = %s",
            (user["id"],),
        )
        assert cur.fetchone() == (storage_path, hashlib.sha256(content).hexdigest())


def test_resume_replacement_deletes_the_previous_object(client, monkeypatch):
    login(client)
    uploads = []
    deletions = []
    monkeypatch.setattr(
        "routes.resume.upload_resume_file",
        lambda path, _content, _content_type: uploads.append(path),
    )
    monkeypatch.setattr("routes.resume.delete_resume_file", deletions.append)

    first = client.put(
        "/api/resume",
        data=multipart_resume(b"First resume source text. " * 5),
        content_type="multipart/form-data",
    )
    second = client.put(
        "/api/resume",
        data=multipart_resume(b"Second resume source text. " * 5),
        content_type="multipart/form-data",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(uploads) == 2
    assert uploads[0] != uploads[1]
    assert deletions == [uploads[0]]


def test_resume_upload_failure_does_not_create_database_metadata(client, monkeypatch):
    login(client)

    def fail_upload(_path, _content, _content_type):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("routes.resume.upload_resume_file", fail_upload)
    response = client.put(
        "/api/resume",
        data=multipart_resume(b"Backend resume source text. " * 5),
        content_type="multipart/form-data",
    )

    assert response.status_code == 502
    assert response.get_json() == {"error": "could not store resume file"}
    assert client.get("/api/resume").status_code == 404


def test_resume_file_download_is_owned_and_short_lived(client, monkeypatch, _db):
    user = login(client)
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumes (user_id, storage_path, original_filename)
            VALUES (%s, %s, %s)
            """,
            (user["id"], f"{user['id']}/source.pdf", "resume.pdf"),
        )

    monkeypatch.setattr(
        "routes.resume.create_resume_download_url",
        lambda path, expires_in: f"https://signed.example/{path}?ttl={expires_in}",
    )
    response = client.get("/api/resume/file")

    assert response.status_code == 200
    assert response.get_json()["expires_in"] == 60
    assert response.get_json()["filename"] == "resume.pdf"
    assert response.headers["Cache-Control"] == "no-store"


def test_resume_file_delete_preserves_extracted_resume_data(client, monkeypatch, _db):
    user = login(client)
    storage_path = f"{user['id']}/source.pdf"
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumes (
                user_id, skills, resume_text, storage_path, original_filename,
                file_mime_type, file_size_bytes, file_sha256, file_uploaded_at
            )
            VALUES (%s, '["Python"]', 'Saved resume text', %s, 'resume.pdf',
                    'application/pdf', 1234, 'abc123', now())
            """,
            (user["id"], storage_path),
        )

    deleted = []
    monkeypatch.setattr("routes.resume.delete_resume_file", deleted.append)

    response = client.delete("/api/resume/file")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert deleted == [storage_path]
    with _db.cursor() as cur:
        cur.execute(
            """
            SELECT skills, resume_text, storage_path, original_filename,
                   file_mime_type, file_size_bytes, file_sha256, file_uploaded_at
            FROM resumes WHERE user_id = %s
            """,
            (user["id"],),
        )
        assert cur.fetchone() == (["Python"], "Saved resume text", None, None, None, None, None, None)


def test_resume_file_delete_returns_not_found_without_saved_file(client, monkeypatch):
    login(client)
    deleted = []
    monkeypatch.setattr("routes.resume.delete_resume_file", deleted.append)

    response = client.delete("/api/resume/file")

    assert response.status_code == 404
    assert response.get_json() == {"error": "no resume file found"}
    assert deleted == []


def test_resume_file_delete_keeps_success_when_storage_cleanup_fails(client, monkeypatch, _db):
    user = login(client)
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumes (user_id, skills, storage_path, original_filename)
            VALUES (%s, '["Python"]', %s, 'resume.pdf')
            """,
            (user["id"], f"{user['id']}/source.pdf"),
        )

    def fail_delete(_path):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("routes.resume.delete_resume_file", fail_delete)

    response = client.delete("/api/resume/file")

    assert response.status_code == 200
    with _db.cursor() as cur:
        cur.execute("SELECT skills, storage_path FROM resumes WHERE user_id = %s", (user["id"],))
        assert cur.fetchone() == (["Python"], None)


def test_resume_delete_removes_all_derived_data_and_resets_matches(client, monkeypatch, _db):
    user = login(client)
    storage_path = f"{user['id']}/source.pdf"
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumes (user_id, skills, resume_text, storage_path, original_filename)
            VALUES (%s, '["Python"]', 'Saved resume text', %s, 'resume.pdf')
            """,
            (user["id"], storage_path),
        )
        cur.execute(
            "INSERT INTO resume_headers (user_id, full_name) VALUES (%s, 'Resume Owner')",
            (user["id"],),
        )
        cur.execute(
            """
            INSERT INTO resume_entries (user_id, kind, title)
            VALUES (%s, 'project', 'API') RETURNING id
            """,
            (user["id"],),
        )
        entry_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_bullets (entry_id, user_id, text, content_hash)
            VALUES (%s, %s, 'Built a Python API', %s)
            """,
            (entry_id, user["id"], hashlib.sha256(b"built a python api").hexdigest()),
        )
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills, match_score, match_detail)
            VALUES (%s, 'Python developer', 'Developer', '["Python"]', 100, '{"matched":["Python"]}')
            RETURNING id
            """,
            (user["id"],),
        )
        job_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, status, model, max_steps, completed_at)
            VALUES (%s, %s, 'completed', 'test-model', 4, now())
            """,
            (user["id"], job_id),
        )

    deleted = []
    monkeypatch.setattr("routes.resume.delete_resume_file", deleted.append)

    response = client.delete("/api/resume")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert deleted == [storage_path]
    assert client.get("/api/resume").status_code == 404

    with _db.cursor() as cur:
        for table in ("resume_headers", "resume_entries", "resume_bullets"):
            cur.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (user["id"],))
            assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM tailoring_runs WHERE user_id = %s", (user["id"],))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT match_score, match_detail FROM jobs WHERE id = %s", (job_id,))
        score, detail = cur.fetchone()
        assert float(score) == 0
        assert detail["matched"] == []
        assert detail["missing"] == ["Python"]


def test_resume_delete_refuses_while_tailoring_is_running(client, monkeypatch, _db):
    user = login(client)
    storage_path = f"{user['id']}/source.pdf"
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resumes (user_id, skills, resume_text, storage_path, original_filename)
            VALUES (%s, '["Python"]', 'Saved resume text', %s, 'resume.pdf')
            """,
            (user["id"], storage_path),
        )
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills)
            VALUES (%s, 'Python developer', 'Developer', '["Python"]') RETURNING id
            """,
            (user["id"],),
        )
        job_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, status, model, max_steps)
            VALUES (%s, %s, 'running', 'test-model', 4)
            """,
            (user["id"], job_id),
        )

    deleted = []
    monkeypatch.setattr("routes.resume.delete_resume_file", deleted.append)

    response = client.delete("/api/resume")

    assert response.status_code == 409
    assert "active tailoring run" in response.get_json()["error"]
    assert deleted == []
    assert client.get("/api/resume").status_code == 200
