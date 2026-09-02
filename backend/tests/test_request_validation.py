import pytest

from routes.request_validation import normalize_optional_http_url


USER = {"username": "jsonvalidationuser", "password": "pw123456"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (" HTTPS://Example.COM:443/jobs/42#apply ", "https://example.com/jobs/42"),
    ],
)
def test_source_url_normalization(value, expected):
    normalized, error = normalize_optional_http_url(value)

    assert error is None
    assert normalized == expected


@pytest.mark.parametrize(
    "value",
    [123, "javascript:alert(1)", "ftp://example.com/job", "https://user:pass@example.com/job", "https://example.com:invalid/job"],
)
def test_source_url_rejects_invalid_values(value):
    normalized, error = normalize_optional_http_url(value)

    assert normalized is None
    assert error == "source_url must be a valid HTTP or HTTPS URL"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/jobs/drafts"),
        ("PATCH", "/api/jobs/00000000-0000-0000-0000-000000000000"),
        ("PUT", "/api/resume"),
        ("POST", "/api/resume/parse"),
    ],
)
def test_json_object_routes_reject_malformed_json_and_arrays(client, method, path):
    client.post("/api/auth/signup", json=USER)
    assert client.post("/api/auth/login", json=USER).status_code == 200

    malformed = client.open(
        path,
        method=method,
        data="{",
        headers={"Content-Type": "application/json"},
    )
    array = client.open(path, method=method, json=[])

    assert malformed.status_code == 400
    assert malformed.get_json() == {"error": "JSON object required"}
    assert array.status_code == 400
    assert array.get_json() == {"error": "JSON object required"}


def test_analysis_routes_reject_non_text_fields_before_openai(client, monkeypatch):
    client.post("/api/auth/signup", json=USER)
    assert client.post("/api/auth/login", json=USER).status_code == 200
    calls = []

    monkeypatch.setattr(
        "routes.jobs.analyze_job_description",
        lambda text, **_kwargs: calls.append(text),
    )
    monkeypatch.setattr(
        "routes.resume.analyze_resume",
        lambda text, **_kwargs: calls.append(text),
    )

    job = client.post("/api/jobs/drafts", json={"description": []})
    resume = client.post("/api/resume/parse", json={"text": []})

    assert job.status_code == 400
    assert job.get_json() == {"error": "Description must be text"}
    assert resume.status_code == 400
    assert resume.get_json() == {"error": "Resume text must be text"}
    assert calls == []
