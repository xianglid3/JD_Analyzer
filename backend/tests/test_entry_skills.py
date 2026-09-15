"""Letting the user correct us.

The fit engine reads a resume, and a resume is a lossy artefact — what someone typed in one
sitting, not the whole truth about them. Two failures came from treating it as ground truth:

  * a gap the user does not actually have, with no way to fix it short of editing the PDF;
  * a skill proven only by a keyword list, which no bullet demonstrates and nothing could
    authorise a bullet to say.

Both are the same missing fact: which project the skill belongs to. Only the user has it.
"""

from uuid import uuid4

import pytest

from db import get_cursor
from services.resume_evidence import entry_skills, save_entry_skills, skills_affirmed_for_bullets
from services.skill_evidence import EXPLICIT, NONE, load_evidence_bullets, evaluate_requirement
from services.tailoring_agent import bullets_by_entry
from services.tailoring_plan import agent_candidates, build_tailoring_plan


@pytest.fixture
def resume(_db):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
            (f"affirmer-{uuid4().hex[:8]}",),
        )
        user_id = cur.fetchone()[0]
        entries = {}
        for order, (kind, title) in enumerate([("project", "Calendar Map"), ("project", "Study Buddy")]):
            cur.execute(
                """
                INSERT INTO resume_entries (user_id, kind, title, sort_order)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (user_id, kind, title, order),
            )
            entry_id = cur.fetchone()[0]
            entries[title] = str(entry_id)
            cur.execute(
                """
                INSERT INTO resume_bullets (entry_id, user_id, text, content_hash, sort_order)
                VALUES (%s, %s, %s, %s, 0)
                """,
                (entry_id, user_id, f"Shipped the {title} release on schedule", f"h-{title}"),
            )
    return {"user_id": user_id, "entries": entries}


def test_a_gap_the_user_does_not_have_can_be_corrected(resume):
    with get_cursor() as cur:
        before = load_evidence_bullets(cur, resume["user_id"])
    assert evaluate_requirement("algorithms", before)["state"] == NONE

    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "algorithms", [resume["entries"]["Calendar Map"]])

    with get_cursor() as cur:
        after = load_evidence_bullets(cur, resume["user_id"])
    assert evaluate_requirement("algorithms", after)["state"] == EXPLICIT


def test_the_claim_is_scoped_to_the_entry_it_was_made_about(resume):
    """Using Python on one project says nothing about another."""
    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "python", [resume["entries"]["Calendar Map"]])
        cur.execute(
            "SELECT id, entry_id FROM resume_bullets WHERE user_id = %s", (resume["user_id"],)
        )
        rows = cur.fetchall()

    calendar = [b for b, e in rows if str(e) == resume["entries"]["Calendar Map"]]
    study = [b for b, e in rows if str(e) == resume["entries"]["Study Buddy"]]

    with get_cursor() as cur:
        assert skills_affirmed_for_bullets(cur, resume["user_id"], calendar) == {"python"}
        assert skills_affirmed_for_bullets(cur, resume["user_id"], study) == set()


def test_withdrawing_the_claim_removes_it(resume):
    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "rust", [resume["entries"]["Study Buddy"]])
    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "rust", [])
    with get_cursor() as cur:
        assert entry_skills(cur, resume["user_id"]) == {}


def test_a_keyword_only_skill_becomes_real_rewrite_work(resume):
    """The payoff. Before the user says where it belongs, nothing can be written; after, a
    bullet in that entry is an approved target."""
    assessment_before = {"requirements": [{
        "requirement": "python", "state": EXPLICIT, "importance": "required",
        "evidence": [{"bullet_id": None, "text": "python"}],   # a keyword list entry
    }]}
    plan = build_tailoring_plan(assessment_before)
    assert [i["action"] for i in plan] == ["only_in_skills"]
    assert agent_candidates(plan) == []

    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "python", [resume["entries"]["Calendar Map"]])
        grouped = bullets_by_entry(cur, resume["user_id"])
        evidence = load_evidence_bullets(cur, resume["user_id"])

    scored = evaluate_requirement("python", evidence)
    assessment_after = {"requirements": [{
        "requirement": "python", "state": scored["state"], "importance": "required",
        "evidence": scored["evidence"],
    }]}
    plan = build_tailoring_plan(assessment_after, bullets_by_entry=grouped)

    assert [i["action"] for i in plan] == ["show_in_bullet"]
    candidate = agent_candidates(plan)[0]
    assert "Calendar Map" in candidate["targets"][0]["weakness"]
    assert candidate["targets"][0]["text"] == "Shipped the Calendar Map release on schedule"


def test_the_route_records_the_claim_and_rescores(client, monkeypatch, _db):
    from uuid import uuid4 as _uuid4

    credentials = {"username": f"routeaffirm{_uuid4().hex[:6]}", "password": "pw123456"}
    client.post("/api/auth/signup", json=credentials)
    assert client.post("/api/auth/login", json=credentials).status_code == 200

    with get_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (credentials["username"],))
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_entries (user_id, kind, title, sort_order)
            VALUES (%s, 'project', 'Calendar Map', 0) RETURNING id
            """,
            (user_id,),
        )
        entry_id = str(cur.fetchone()[0])

    response = client.put("/api/resume/entry-skills", json={
        "skill": "Algorithms", "entry_ids": [entry_id],
    })
    assert response.status_code == 200
    assert response.get_json()["entries"] == [entry_id]

    with get_cursor() as cur:
        assert entry_skills(cur, user_id) == {entry_id: {"algorithms"}}


def test_the_route_rejects_an_id_that_is_not_a_uuid(client, _db):
    from uuid import uuid4 as _uuid4

    credentials = {"username": f"badid{_uuid4().hex[:6]}", "password": "pw123456"}
    client.post("/api/auth/signup", json=credentials)
    client.post("/api/auth/login", json=credentials)

    response = client.put("/api/resume/entry-skills", json={"skill": "sql", "entry_ids": ["1"]})
    assert response.status_code == 400


def test_one_user_cannot_attach_a_skill_to_another_users_entry(client, resume, _db):
    from uuid import uuid4 as _uuid4

    credentials = {"username": f"intruder{_uuid4().hex[:6]}", "password": "pw123456"}
    client.post("/api/auth/signup", json=credentials)
    client.post("/api/auth/login", json=credentials)

    response = client.put("/api/resume/entry-skills", json={
        "skill": "sql", "entry_ids": [resume["entries"]["Calendar Map"]],
    })

    assert response.status_code == 200
    assert response.get_json()["entries"] == []        # scoped in SQL, silently ignored
    with get_cursor() as cur:
        assert entry_skills(cur, resume["user_id"]) == {}


def test_an_affirmed_skill_survives_being_written_to_the_candidate_table(resume, monkeypatch, _db):
    """The regression that produced a 500 at run creation.

    Every test that produced a `show_in_bullet` candidate called `build_tailoring_plan`
    directly, so nothing ever tried to STORE one — and the planner had been widened without
    widening the CHECK constraint that records what it hands out. This goes through
    `start_run`, which is where it broke.
    """
    from services.tailoring_agent import start_run
    from services import tailoring_candidates as candidates_state

    with get_cursor(commit=True) as cur:
        save_entry_skills(cur, resume["user_id"], "python", [resume["entries"]["Calendar Map"]])
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills, requirements)
            VALUES (%s, 'jd', 'Engineer', '["python"]'::jsonb,
                    '[{"skill": "python", "importance": "required", "type": "skill"}]'::jsonb)
            RETURNING id
            """,
            (resume["user_id"],),
        )
        job_id = cur.fetchone()[0]

    started = start_run(get_cursor, resume["user_id"], job_id)
    assert "error" not in started, started
    run_id = started["run_id"]

    with get_cursor() as cur:
        stored = candidates_state.load(cur, run_id)

    assert [item["action"] for item in stored] == ["show_in_bullet"]


def test_every_action_the_planner_hands_out_can_be_stored(_db):
    """The general form: the candidate table has to accept whatever `agent_candidates` emits,
    or widening the planner is a 500 nobody sees until a real run."""
    from services.tailoring_plan import build_tailoring_plan

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conname = 'tailoring_candidates_action_check'
            """
        )
        allowed = cur.fetchone()[0]

    for action in ("rewrite", "strengthen", "confirm", "show_in_bullet"):
        assert f"'{action}'" in allowed, f"the table cannot store a {action!r} candidate"
    assert build_tailoring_plan(None) == []
