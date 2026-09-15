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
    monkeypatch.setattr("routes.jobs.analyze_job_description", lambda _text, **_kwargs: extracted_job())
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


def test_requirements_can_be_corrected_at_review(client, monkeypatch, _db):
    """Extraction gets importance wrong often enough that a bad requirement would otherwise
    score every future match against this job."""
    draft = analyze_draft(client, monkeypatch).get_json()

    response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
        "title": "Platform Engineer",
        "requirements": [
            {"skill": "Kubernetes", "importance": "required"},
            {"skill": "Jira", "importance": "nice_to_have"},
        ],
    })

    assert response.status_code == 201
    job = client.get(f"/api/jobs/{response.get_json()['id']}").get_json()
    assert job["requirements"] == [
        {"skill": "Kubernetes", "importance": "required", "type": "skill"},
        {"skill": "Jira", "importance": "nice_to_have", "type": "skill"},
    ]
    # the flat list is regenerated from the same edit, so the two cannot disagree
    assert job["skills"] == ["Kubernetes", "Jira"]


def test_eligibility_is_kept_but_never_scored(client, monkeypatch, _db):
    """A clearance is a gate on applying, not a wording problem: it must not drag capability
    down and must never reach tailoring."""
    draft = analyze_draft(client, monkeypatch).get_json()

    response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
        "title": "Platform Engineer",
        "requirements": [
            {"skill": "Kubernetes", "importance": "required"},
            {"skill": "US citizenship required", "importance": "required", "type": "eligibility"},
        ],
    })

    assert response.status_code == 201
    job = client.get(f"/api/jobs/{response.get_json()['id']}").get_json()

    assert job["requirements"][1]["type"] == "eligibility"
    # carried through for the user to judge...
    assert job["match_detail"]["eligibility"] == ["US citizenship required"]
    # ...but absent from everything that gets scored
    scored = [r["requirement"] for r in job["match_detail"]["requirements"]]
    assert scored == ["Kubernetes"]
    # and out of the flat skill list, which older scoring paths fall back to
    assert job["skills"] == ["Kubernetes"]


def test_an_unknown_requirement_type_is_rejected(client, monkeypatch, _db):
    draft = analyze_draft(client, monkeypatch).get_json()

    response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
        "title": "Platform Engineer",
        "requirements": [{"skill": "Kubernetes", "type": "vibes"}],
    })

    assert response.status_code == 400
    assert "type must be" in response.get_json()["error"]


def test_edited_requirements_are_validated(client, monkeypatch):
    draft = analyze_draft(client, monkeypatch).get_json()

    for body, expected in [
        ({"requirements": "nope"}, "requirements must be an array"),
        ({"requirements": [{"skill": ""}]}, "each requirement needs a skill"),
        ({"requirements": [{"skill": "Go", "importance": "critical"}]}, "importance must be required, preferred, or nice_to_have"),
    ]:
        response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm",
                               json={"title": "Platform Engineer", **body})
        assert response.status_code == 400
        assert response.get_json()["error"] == expected


def test_duplicate_requirements_are_collapsed(client, monkeypatch):
    draft = analyze_draft(client, monkeypatch).get_json()

    response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
        "title": "Platform Engineer",
        "requirements": [{"skill": "Go"}, {"skill": "go"}],
    })

    job = client.get(f"/api/jobs/{response.get_json()['id']}").get_json()
    assert job["skills"] == ["Go"]


# ── requirement conditions survive the review screen ────────────────────────

def test_a_condition_is_stored_as_one_requirement(client, monkeypatch, _db):
    """"One of Java, Python or C++" must not be flattened back into three."""
    draft = analyze_draft(client, monkeypatch).get_json()

    response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
        "title": "Platform Engineer",
        "requirements": [{
            "importance": "required",
            "source_text": "experience in one of Java, Python or C++",
            "condition": {"operator": "any_of", "minimum": 1, "items": ["Java", "Python", "C++"]},
        }],
    })

    assert response.status_code == 201
    job = client.get(f"/api/jobs/{response.get_json()['id']}").get_json()

    assert len(job["requirements"]) == 1
    assert job["requirements"][0]["condition"]["items"] == ["Java", "Python", "C++"]
    # the flat view still lists every alternative, so older scoring paths keep working
    assert job["skills"] == ["Java", "Python", "C++"]
    # and it is scored as one requirement, not three
    assert len(job["match_detail"]["requirements"]) == 1
    assert job["match_detail"]["requirements"][0]["requirement"] == \
        "experience in one of Java, Python or C++"


def test_condition_shapes_are_validated(client, monkeypatch):
    draft = analyze_draft(client, monkeypatch).get_json()

    for condition, expected in [
        ({"operator": "some_of", "items": ["Go"]}, "operator must be any_of or all_of"),
        ({"operator": "any_of", "items": []}, "condition needs at least one item"),
        ({"operator": "any_of", "items": ["Go", ""]}, "each condition item must be non-empty text"),
        ({"operator": "any_of", "items": ["Go"], "minimum": 0}, "minimum must be a positive integer"),
        ({"operator": "any_of", "items": ["Go"], "minimum": 2}, "minimum cannot exceed the number of items"),
        ({"operator": "any_of", "items": ["S" * 101]}, "each skill must be 100 characters or fewer"),
    ]:
        response = client.post(f"/api/jobs/drafts/{draft['id']}/confirm", json={
            "title": "Platform Engineer",
            "requirements": [{"condition": condition}],
        })
        assert response.status_code == 400, condition
        assert response.get_json()["error"] == expected


def test_an_eligibility_condition_may_be_a_full_sentence(client, monkeypatch, _db):
    """Eligibility is the posting's own wording, quoted. The 100-character rule exists to keep
    skill NAMES usable as match keys, and an eligibility gate is never matched against
    evidence — so it was rejecting the exact thing it is supposed to carry."""
    draft_id = analyze_draft(client, monkeypatch).get_json()["id"]
    sentence = (
        "Individuals who are completing or have recently completed a Bachelor's or above "
        "degree in computer science or a related discipline, and who can commit to an "
        "onboarding date before the end of the calendar year"
    )
    assert len(sentence) > 100

    response = client.post(f"/api/jobs/drafts/{draft_id}/confirm", json={
        "title": "Engineer",
        "requirements": [
            {"skill": "python", "importance": "required"},
            {"skill": sentence, "importance": "required", "type": "eligibility"},
        ],
    })

    assert response.status_code == 201
    job = client.get(f"/api/jobs/{response.get_json()['id']}").get_json()
    gates = [r for r in job["requirements"] if r.get("type") == "eligibility"]
    assert [g["skill"] for g in gates] == [sentence]
    # carried, and still excluded from the score
    assert sentence not in (job["match_detail"] or {}).get("missing", [])


def test_a_skill_name_is_still_held_to_the_short_limit(client, monkeypatch, _db):
    draft_id = analyze_draft(client, monkeypatch).get_json()["id"]
    response = client.post(f"/api/jobs/drafts/{draft_id}/confirm", json={
        "title": "Engineer",
        "requirements": [{"skill": "x" * 101, "importance": "required"}],
    })
    assert response.status_code == 400
    assert "skill" in response.get_json()["error"]
