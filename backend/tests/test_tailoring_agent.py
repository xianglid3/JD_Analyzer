"""The tailoring agent: the loop, and the grounding checks that gate it.

The model is scripted here — these tests are about what the backend does with what the
model says, especially when it says something it isn't entitled to.
"""

import json
import types

import pytest

from db import get_cursor
from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services import tailoring_agent
from services.tailoring_agent import GroundingError, load_run, run_tailoring, verify_citation


BULLETS = [
    "Deployed services to Kubernetes across three regions",
    "Built an ingestion pipeline in Python processing 2M events daily",
]


def call(name, arguments, call_id="call-1"):
    return types.SimpleNamespace(
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def response(tool_calls=None, content=None, prompt=100, completion=20):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=types.SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def script(monkeypatch, *responses):
    """Play a fixed sequence of model turns, and record the messages it was sent."""
    sent = []
    queue = list(responses)

    def fake_complete(messages):
        sent.append(list(messages))
        return queue.pop(0)

    monkeypatch.setattr(tailoring_agent, "complete", fake_complete)
    return sent


@pytest.fixture
def fixtures(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, resume_bullets, resume_entries, jobs, resumes, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('agentuser', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO jobs (user_id, raw_description, title, company_name, summary, skills)
            VALUES (%s, 'x', 'Platform Engineer', 'Globex', 'Runs the deploy pipeline',
                    '["kubernetes", "terraform"]'::jsonb)
            RETURNING id
            """,
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", title="Intern", bullets=BULLETS)
        ]))
        cur.execute("SELECT id, text FROM resume_bullets WHERE user_id = %s ORDER BY sort_order", (user_id,))
        bullets = {text: str(bid) for bid, text in cur.fetchall()}
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "bullets": bullets}


@pytest.fixture
def k8s_bullet(fixtures):
    return fixtures["bullets"]["Deployed services to Kubernetes across three regions"]


# ── the happy path ───────────────────────────────────────────────────────────

def test_search_then_grounded_edit_is_recorded(monkeypatch, fixtures, k8s_bullet, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Deployed and operated multi-region Kubernetes services",
            "evidence_bullet_ids": [k8s_bullet],
        }, "c2")]),
        response(content="Covered Kubernetes; Terraform is missing."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert result["status"] == "completed"
    assert result["steps_used"] == 3

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert len(run["edits"]) == 1
    assert run["edits"][0]["proposed_text"].startswith("Deployed and operated")
    assert run["edits"][0]["evidence"][0]["bullet_id"] == k8s_bullet
    assert run["edits"][0]["original_text"] == "Deployed services to Kubernetes across three regions"


def test_run_records_tokens_and_trace(monkeypatch, fixtures, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "terraform"}, "c1")]),
        response([call("flag_gap", {"requirement": "Terraform", "note": "no infrastructure-as-code work"}, "c2")]),
        response(content="Done."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])

    assert run["input_tokens"] == 300 and run["output_tokens"] == 60
    assert [t["tool"] for t in run["trace"]] == ["search_resume", "flag_gap"]
    assert run["gaps"][0]["requirement"] == "Terraform"
    assert run["gaps"][0]["searched"] == ["terraform"]


# ── grounding: the backend refuses what the model isn't entitled to ──────────

def test_edit_without_a_prior_search_is_rejected(monkeypatch, fixtures, k8s_bullet, _db):
    """The bullet is real and is the user's — but nothing in this run retrieved it."""
    script(
        monkeypatch,
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Ran Kubernetes at scale",
            "evidence_bullet_ids": [k8s_bullet],
        }, "c1")]),
        response(content="Understood."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["edits"] == []
    assert run["trace"][0]["status"] == "failed"
    assert "search" in run["trace"][0]["error"]


def test_rejection_is_returned_to_the_model(monkeypatch, fixtures, k8s_bullet):
    sent = script(
        monkeypatch,
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Ran Kubernetes at scale",
            "evidence_bullet_ids": [k8s_bullet],
        }, "c1")]),
        response(content="I'll search first."),
    )

    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    tool_reply = json.loads(sent[-1][-1]["content"])
    assert "error" in tool_reply          # the model is told why, so it can recover


def test_cannot_cite_another_users_bullet(monkeypatch, fixtures, _db):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('agentother', 'x') RETURNING id")
        other_id = cur.fetchone()[0]
        save_resume_evidence(cur, other_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", bullets=["Managed Terraform for 40 services"])
        ]))
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s", (other_id,))
        stolen = str(cur.fetchone()[0])
    _db.commit()

    script(
        monkeypatch,
        response([call("search_resume", {"query": "terraform"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Terraform",
            "bullet_id": stolen,
            "proposed_text": "Managed Terraform for 40 services",
            "evidence_bullet_ids": [stolen],
        }, "c2")]),
        response(content="Understood."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["edits"] == []
    assert "not part of your resume" in run["trace"][1]["error"]


def test_edit_with_no_evidence_ids_is_rejected(monkeypatch, fixtures, k8s_bullet, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Ran Kubernetes at scale",
            "evidence_bullet_ids": [],
        }, "c2")]),
        response(content="Understood."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["edits"] == []
    assert "evidence" in run["trace"][1]["error"]


def test_invented_bullet_id_is_rejected(monkeypatch, fixtures, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": "00000000-0000-0000-0000-000000000000",
            "proposed_text": "Ran Kubernetes at scale",
            "evidence_bullet_ids": ["00000000-0000-0000-0000-000000000000"],
        }, "c2")]),
        response(content="Understood."),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["edits"] == []


def test_verify_citation_returns_the_call_that_found_it(monkeypatch, fixtures, k8s_bullet, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response(content="done"),
    )
    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        tool_call_id = verify_citation(cur, fixtures["user_id"], result["run_id"], k8s_bullet)
        cur.execute("SELECT tool_name FROM tool_calls WHERE id = %s", (tool_call_id,))
        assert cur.fetchone()[0] == "search_resume"


# ── loop control ─────────────────────────────────────────────────────────────

def test_step_cap_stops_an_endless_searcher(monkeypatch, fixtures, _db):
    searching = [response([call("search_resume", {"query": f"q{i}"}, f"c{i}")]) for i in range(10)]
    script(monkeypatch, *searching)

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"], max_steps=3)

    assert result["status"] == "limit_reached"
    assert result["steps_used"] == 3


def test_step_count_varies_with_what_the_search_returns(monkeypatch, fixtures, k8s_bullet):
    """One run finds evidence immediately; the other searches again first. Same code path."""
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response(content="found it"),
    )
    quick = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    script(
        monkeypatch,
        response([call("search_resume", {"query": "terraform"}, "c1")]),
        response([call("search_resume", {"query": "infrastructure as code"}, "c2")]),
        response([call("search_resume", {"query": "cloud provisioning"}, "c3")]),
        response(content="nothing there"),
    )
    thorough = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert quick["steps_used"] == 2
    assert thorough["steps_used"] == 4


def test_model_failure_marks_the_run_failed(monkeypatch, fixtures, _db):
    def boom(_messages):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(tailoring_agent, "complete", boom)

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert result["status"] == "failed"
    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["error_code"] == "model_call_failed"


def test_another_users_job_is_not_runnable(monkeypatch, fixtures, _db):
    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('agentthird', 'x') RETURNING id")
        other_id = cur.fetchone()[0]
    _db.commit()

    assert run_tailoring(get_cursor, other_id, fixtures["job_id"]) is None


def test_run_without_evidence_stops_before_spending(monkeypatch, fixtures, _db):
    with _db.cursor() as cur:
        cur.execute("DELETE FROM resume_bullets WHERE user_id = %s", (fixtures["user_id"],))
    _db.commit()
    called = []
    monkeypatch.setattr(tailoring_agent, "complete", lambda _m: called.append(1))

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert result == {"error": "no_evidence"}
    assert called == []


def test_load_run_is_ownership_scoped(monkeypatch, fixtures, _db):
    script(monkeypatch, response(content="nothing to do"))
    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('agentsnoop', 'x') RETURNING id")
        snoop_id = cur.fetchone()[0]
        assert load_run(cur, snoop_id, result["run_id"]) is None


def test_unexpected_tool_error_fails_the_run_cleanly(monkeypatch, fixtures, _db):
    """A grounding rejection is feedback; anything else ends the run — but the run row
    must still be closed out, not left at 'running'."""
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response(content="unreachable"),
    )

    def broken(_cur, _user_id, _run_id, _arguments):
        raise RuntimeError("relation does not exist")

    monkeypatch.setitem(tailoring_agent.TOOL_IMPLEMENTATIONS, "search_resume", broken)

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert result["status"] == "failed"
    with _db.cursor() as cur:
        cur.execute("SELECT status, error_code, completed_at FROM tailoring_runs WHERE id = %s", (result["run_id"],))
        status, error_code, completed_at = cur.fetchone()
    assert status == "failed" and error_code == "tool_execution_failed" and completed_at is not None


def test_brief_carries_the_assessment_so_steps_arent_spent_rediscovering_it(monkeypatch, fixtures):
    """A live run spent its whole budget searching and produced nothing. The agent now starts
    with the deterministic per-requirement assessment."""
    sent = script(monkeypatch, response(content="nothing to do"))

    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    brief = sent[0][1]["content"]
    assert "Assessment of this candidate" in brief
    assert "kubernetes" in brief.lower()
    assert "EXPLICIT" in brief or "INFERRED" in brief or "NONE" in brief
    assert "capability" in brief.lower()


def test_the_brief_never_leaks_bullet_ids(monkeypatch, fixtures, k8s_bullet):
    """If the assessment named ids, the grounding check could be satisfied by the prompt
    rather than by an actual search."""
    sent = script(monkeypatch, response(content="done"))

    run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    assert k8s_bullet not in sent[0][1]["content"]


def test_gap_is_refused_when_this_run_already_found_evidence(monkeypatch, fixtures, _db):
    """A live run flagged 'ai' as a gap in a note that described the candidate's AI work.
    A gap asserts an absence; the run's own search contradicted it."""
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("flag_gap", {"requirement": "kubernetes", "note": "candidate has k8s work"}, "c2")]),
        response(content="understood"),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["gaps"] == []
    assert "propose an edit citing them" in run["trace"][1]["error"]


def test_gap_is_allowed_when_the_search_found_nothing(monkeypatch, fixtures, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "terraform"}, "c1")]),
        response([call("flag_gap", {"requirement": "terraform", "note": "no IaC work"}, "c2")]),
        response(content="done"),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["gaps"][0]["requirement"] == "terraform"


def test_a_rewrite_naming_an_uncited_technology_is_rejected(monkeypatch, fixtures, k8s_bullet, _db):
    """Citing a real bullet is not enough — the sentence has to stay inside it."""
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Deployed Kubernetes services on AWS with Terraform",
            "evidence_bullet_ids": [k8s_bullet],
        }, "c2")]),
        response(content="understood"),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert run["edits"] == []
    assert "does not appear in the evidence" in run["trace"][1]["error"]


def test_a_faithful_rewrite_still_passes(monkeypatch, fixtures, k8s_bullet, _db):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": k8s_bullet,
            "proposed_text": "Operated Kubernetes services across three regions",
            "evidence_bullet_ids": [k8s_bullet],
        }, "c2")]),
        response(content="done"),
    )

    result = run_tailoring(get_cursor, fixtures["user_id"], fixtures["job_id"])

    with _db.cursor() as cur:
        run = load_run(cur, fixtures["user_id"], result["run_id"])
    assert len(run["edits"]) == 1


def test_sweep_closes_silent_runs_nobody_revisits(_db, fixtures):
    """reap_abandoned_run only fires when that run is opened; a stranded run nobody looks at
    would stay 'running' forever."""
    from services.tailoring_agent import sweep_abandoned_runs

    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, heartbeat_at)
            VALUES (%s, %s, 'test', 8, now() - interval '1 hour') RETURNING id
            """,
            (fixtures["user_id"], fixtures["job_id"]),
        )
        stranded = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, heartbeat_at)
            VALUES (%s, %s, 'test', 8, now()) RETURNING id
            """,
            (fixtures["user_id"], fixtures["job_id"]),
        )
        alive = str(cur.fetchone()[0])

        swept = sweep_abandoned_runs(cur)

        assert stranded in swept and alive not in swept
        cur.execute("SELECT status FROM tailoring_runs WHERE id = %s", (alive,))
        assert cur.fetchone()[0] == "running"
