"""Routes for the structured resume: extract (preview), review, save, read back."""

import pytest

from services.openai_services import ResumeEntryExtraction, ResumeStructure


USER = {"username": "structureuser", "password": "pw123456"}
OTHER = {"username": "structureother", "password": "pw123456"}

RESUME_TEXT = (
    "Backend Intern, Acme, Jun 2024 - Aug 2024. "
    "Shipped an ingestion pipeline in Python. Cut p95 latency by 30 percent. " * 3
)


def login(client, user=USER):
    client.post("/api/auth/signup", json=user)
    assert client.post("/api/auth/login", json=user).status_code == 200


def fake_structure(*bullets, org="Acme", title="Backend Intern"):
    return ResumeStructure(
        entries=[ResumeEntryExtraction(kind="experience", organization=org, title=title, bullets=list(bullets))],
        skills=["python"],
    )


def payload(*bullets, kind="experience", org="Acme"):
    return {"entries": [{"kind": kind, "organization": org, "title": "Backend Intern", "bullets": list(bullets)}]}


# ── extract: preview only, nothing persisted ─────────────────────────────────

def test_extract_returns_structure_without_saving(client, monkeypatch):
    login(client)
    monkeypatch.setattr(
        "routes.resume.analyze_resume_structure",
        lambda _text, **_kwargs: fake_structure("Shipped the pipeline"),
    )

    response = client.post("/api/resume/structure", json={"text": RESUME_TEXT})

    assert response.status_code == 200
    assert response.get_json()["entries"][0]["bullets"] == ["Shipped the pipeline"]
    # nothing committed yet — the user still has to review and save
    assert client.get("/api/resume/evidence").get_json()["entries"] == []


def test_extract_falls_back_to_saved_resume_text(client, monkeypatch):
    login(client)
    client.put("/api/resume", json={"skills": ["Python"], "resume_text": RESUME_TEXT})
    seen = {}

    def capture(text, **_kwargs):
        seen["text"] = text
        return fake_structure("Shipped it")

    monkeypatch.setattr("routes.resume.analyze_resume_structure", capture)

    response = client.post("/api/resume/structure", json={})

    assert response.status_code == 200
    assert "Acme" in seen["text"] or "Backend Intern" in seen["text"]


def test_extract_without_any_resume_text_is_404(client):
    login(client)
    assert client.post("/api/resume/structure", json={}).status_code == 404


def test_extract_rejects_short_text(client, monkeypatch):
    login(client)
    called = []
    monkeypatch.setattr("routes.resume.analyze_resume_structure", lambda _t, **_kwargs: called.append(1))

    response = client.post("/api/resume/structure", json={"text": "too short"})

    assert response.status_code == 400
    assert called == []          # never reaches the paid call


def test_extract_requires_auth(client):
    assert client.post("/api/resume/structure", json={"text": RESUME_TEXT}).status_code == 401


# ── save + read back ─────────────────────────────────────────────────────────

def test_save_then_read_evidence(client):
    login(client)

    saved = client.put("/api/resume/evidence", json=payload("Shipped the pipeline", "Cut latency"))
    assert saved.status_code == 200
    assert saved.get_json()["saved"] == {"entries": 1, "bullets": 2, "reused_bullets": 0}

    entries = client.get("/api/resume/evidence").get_json()["entries"]
    assert entries[0]["organization"] == "Acme"
    assert [b["text"] for b in entries[0]["bullets"]] == ["Shipped the pipeline", "Cut latency"]
    assert all(b["id"] for b in entries[0]["bullets"])


def test_resaving_reuses_bullet_ids(client):
    login(client)
    client.put("/api/resume/evidence", json=payload("Shipped the pipeline"))
    first = client.get("/api/resume/evidence").get_json()["entries"][0]["bullets"][0]["id"]

    again = client.put("/api/resume/evidence", json=payload("Shipped the pipeline", "Cut latency"))
    second = client.get("/api/resume/evidence").get_json()["entries"][0]["bullets"][0]["id"]

    assert first == second
    assert again.get_json()["saved"]["reused_bullets"] == 1


def test_saving_empty_entries_clears_evidence(client):
    login(client)
    client.put("/api/resume/evidence", json=payload("Shipped the pipeline"))

    response = client.put("/api/resume/evidence", json={"entries": []})

    assert response.status_code == 200
    assert response.get_json()["entries"] == []


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, expected", [
    ({"entries": "nope"}, "entries must be an array"),
    ({"entries": ["nope"]}, "each entry must be an object"),
    ({"entries": [{"kind": "experience", "bullets": "nope"}]}, "bullets must be an array"),
    ({"entries": [{"kind": "experience", "bullets": [42]}]}, "each bullet must be text"),
    ({"entries": [{"kind": "experience", "title": 42}]}, "title must be text or null"),
])
def test_invalid_structure_payloads_are_400(client, body, expected):
    login(client)
    response = client.put("/api/resume/evidence", json=body)
    assert response.status_code == 400
    assert response.get_json()["error"] == expected


def test_unknown_entry_kind_is_400(client):
    login(client)
    response = client.put("/api/resume/evidence", json=payload("x", kind="volunteering"))
    assert response.status_code == 400
    assert "kind" in response.get_json()["error"]


def test_oversized_bullet_is_400(client):
    login(client)
    response = client.put("/api/resume/evidence", json=payload("x" * 501))
    assert response.status_code == 400


def test_caps_are_applied_on_save(client):
    login(client)
    response = client.put("/api/resume/evidence", json=payload(*[f"bullet {i}" for i in range(20)]))
    assert response.get_json()["saved"]["bullets"] == 12


# ── ownership ────────────────────────────────────────────────────────────────

def test_evidence_is_private_to_its_owner(client):
    login(client)
    client.put("/api/resume/evidence", json=payload("Mine only"))
    client.post("/api/auth/logout")

    login(client, OTHER)
    assert client.get("/api/resume/evidence").get_json()["entries"] == []


def test_evidence_routes_require_auth(client):
    assert client.get("/api/resume/evidence").status_code == 401
    assert client.put("/api/resume/evidence", json={"entries": []}).status_code == 401


# ── header ───────────────────────────────────────────────────────────────────

HEADER = {
    "full_name": "Shawn Li",
    "email": "shl362@pitt.edu",
    "phone": "(412) 400-6088",
    "location": "Pittsburgh, PA",
    "links": ["github.com/shawn"],
}


def test_header_saves_and_reads_back(client):
    login(client)

    saved = client.put("/api/resume/evidence", json={**payload("Shipped it"), "header": HEADER})

    assert saved.status_code == 200
    assert saved.get_json()["header"]["full_name"] == "Shawn Li"
    assert client.get("/api/resume/evidence").get_json()["header"]["email"] == "shl362@pitt.edu"


def test_header_is_replaced_not_merged(client):
    login(client)
    client.put("/api/resume/evidence", json={**payload("Shipped it"), "header": HEADER})

    client.put("/api/resume/evidence", json={
        **payload("Shipped it"),
        "header": {"full_name": "Shawn Li", "links": []},
    })

    header = client.get("/api/resume/evidence").get_json()["header"]
    assert header["full_name"] == "Shawn Li" and header["email"] is None


def test_missing_header_is_allowed(client):
    """Evidence is usable without contact details; only rendering needs them."""
    login(client)

    saved = client.put("/api/resume/evidence", json=payload("Shipped it"))

    assert saved.status_code == 200
    assert saved.get_json()["header"]["full_name"] is None


def test_header_is_private_to_its_owner(client):
    login(client)
    client.put("/api/resume/evidence", json={**payload("Mine"), "header": HEADER})
    client.post("/api/auth/logout")

    login(client, OTHER)

    assert client.get("/api/resume/evidence").get_json()["header"]["full_name"] is None


@pytest.mark.parametrize("header, expected", [
    ({"full_name": 42}, "full_name must be text or null"),
    ({"email": "x" * 201}, "email must be 200 characters or fewer"),
    ({"links": "nope"}, "links must be an array of text"),
    ({"links": [42]}, "links must be an array of text"),
])
def test_invalid_headers_are_400(client, header, expected):
    login(client)
    response = client.put("/api/resume/evidence", json={**payload("x"), "header": header})
    assert response.status_code == 400
    assert response.get_json()["error"] == expected


def test_extraction_returns_a_header(client, monkeypatch):
    login(client)
    from services.openai_services import ResumeHeader
    structure = fake_structure("Shipped it")
    structure.header = ResumeHeader(full_name="Shawn Li", email="shl362@pitt.edu", links=["github.com/shawn"])
    monkeypatch.setattr("routes.resume.analyze_resume_structure", lambda _t, **_kwargs: structure)

    body = client.post("/api/resume/structure", json={"text": RESUME_TEXT}).get_json()

    assert body["header"]["full_name"] == "Shawn Li"
    assert body["header"]["links"] == ["github.com/shawn"]
