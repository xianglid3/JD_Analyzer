""""I used this on that project" — recorded, and turned into a proposed edit.

The claim is first-party evidence: the resume was only ever a record of what the user says
about their own work, and this is the same claim made directly. What it is not is a licence to
write anything — every check the agent answers to applies here, and the tests that matter are
the ones proving the user starting it changes who chose the target, not what may be written.
"""

import json
import types

import pytest

from db import get_cursor
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services import surface_skill as surfacing
from services.surface_skill import SurfaceRefused, surface_skill
from services.tailoring_agent import load_run, start_run

BULLET = "Built an ingestion pipeline in Python processing 2M events daily"


@pytest.fixture
def setup(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('surfaceuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, skills)
            VALUES (%s, 'x', 'Platform Engineer', '["kubernetes"]'::jsonb) RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="project", organization="Acme", title="Ingest", bullets=[BULLET])
        ]))
        cur.execute("SELECT id FROM resume_entries WHERE user_id = %s", (user_id,))
        entry_id = str(cur.fetchone()[0])
    _db.commit()

    run_id = start_run(get_cursor, user_id, job_id)["run_id"]
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE tailoring_runs SET status = 'completed' WHERE id = %s", (run_id,))
    return {"user_id": user_id, "job_id": job_id, "entry_id": entry_id, "run_id": run_id}


def rewrite_as(monkeypatch, proposed_text, index=0):
    monkeypatch.setattr(surfacing, "surface_rewrite", lambda **_kwargs: {
        "bullet_index": index, "proposed_text": proposed_text,
    })


def test_the_answer_becomes_an_edit_waiting_for_approval(monkeypatch, setup, _db):
    rewrite_as(monkeypatch, "Built an ingestion pipeline in Python processing 2M events daily, "
                            "tracked end to end with software configuration management")

    with get_cursor(commit=True) as cur:
        outcome = surface_skill(
            cur, setup["user_id"], setup["run_id"],
            skill="software configuration management", entry_id=setup["entry_id"],
            detail="Used software configuration management to track every release of the pipeline",
        )

    assert outcome["edit_id"]
    with _db.cursor() as cur:
        run = load_run(cur, setup["user_id"], setup["run_id"])
    assert [edit["status"] for edit in run["edits"]] == ["proposed"]
    assert "software configuration management" in run["edits"][0]["proposed_text"]


def test_the_skill_is_attached_to_the_entry_the_user_named(monkeypatch, setup, _db):
    """Scoring reads this too, so the affirmation has to survive whatever the rewrite does."""
    rewrite_as(monkeypatch, "Built an ingestion pipeline in Python processing 2M events daily "
                            "under software configuration management")

    with get_cursor(commit=True) as cur:
        surface_skill(cur, setup["user_id"], setup["run_id"],
                      skill="software configuration management", entry_id=setup["entry_id"],
                      detail="Kept every pipeline release under version control and review")

    with _db.cursor() as cur:
        cur.execute(
            "SELECT normalized FROM resume_entry_skills WHERE user_id = %s AND entry_id = %s",
            (setup["user_id"], setup["entry_id"]),
        )
        assert cur.fetchone()[0] == "software configuration management"


def test_an_invented_fact_is_refused_the_same_way_the_agent_would_be(monkeypatch, setup):
    """The user chose the target. They did not thereby license a claim they never made."""
    rewrite_as(monkeypatch, "Built an ingestion pipeline in Python processing 2M events daily "
                            "across a Kubernetes cluster of 40 nodes")

    with get_cursor(commit=True) as cur:
        with pytest.raises(SurfaceRefused) as refusal:
            surface_skill(cur, setup["user_id"], setup["run_id"],
                          skill="software configuration management", entry_id=setup["entry_id"],
                          detail="Tracked releases of the pipeline")

    assert "kubernetes" in str(refusal.value).lower() or "40" in str(refusal.value)


def test_a_bare_confirmation_is_refused_before_any_model_call(monkeypatch, setup):
    called = []
    monkeypatch.setattr(surfacing, "surface_rewrite", lambda **_k: called.append(1))

    with get_cursor(commit=True) as cur:
        with pytest.raises(SurfaceRefused):
            surface_skill(cur, setup["user_id"], setup["run_id"],
                          skill="terraform", entry_id=setup["entry_id"], detail="")

    assert called == [], "an empty answer must not cost a model call"


def test_a_run_still_working_does_not_take_additions(monkeypatch, setup):
    rewrite_as(monkeypatch, "anything")
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE tailoring_runs SET status = 'running' WHERE id = %s", (setup["run_id"],))
        with pytest.raises(SurfaceRefused) as refusal:
            surface_skill(cur, setup["user_id"], setup["run_id"],
                          skill="terraform", entry_id=setup["entry_id"],
                          detail="Used it to provision the staging environment")

    assert "still working" in str(refusal.value)


def test_another_users_run_is_not_found(monkeypatch, setup, _db):
    rewrite_as(monkeypatch, "anything")
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('intruder', 'x') RETURNING id")
        intruder = cur.fetchone()[0]
    _db.commit()

    with get_cursor(commit=True) as cur:
        assert surface_skill(cur, intruder, setup["run_id"], skill="terraform",
                             entry_id=setup["entry_id"], detail="Used it somewhere") is None


def test_the_retrieval_is_recorded_as_the_search_it_is(monkeypatch, setup, _db):
    """A citation has to trace to a real retrieval in this run — that rule is what stops an id
    appearing from nowhere, so this path records one rather than bypassing the check."""
    rewrite_as(monkeypatch, "Built an ingestion pipeline in Python processing 2M events daily "
                            "with software configuration management throughout")

    with get_cursor(commit=True) as cur:
        surface_skill(cur, setup["user_id"], setup["run_id"],
                      skill="software configuration management", entry_id=setup["entry_id"],
                      detail="Every pipeline change went through review and versioning")

    with _db.cursor() as cur:
        cur.execute(
            "SELECT arguments FROM tool_calls WHERE run_id = %s AND tool_name = 'search_resume'",
            (setup["run_id"],),
        )
        arguments = cur.fetchone()[0]
    # the trace never implies the model went looking for this
    assert arguments["source"] == "the entry the user named"
