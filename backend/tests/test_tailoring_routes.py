"""Routes for the tailoring agent: start a run, read it, decide on its proposals."""

import json
import time
import types

import pytest

from services.openai_services import ResumeEntryExtraction, ResumeStructure
from services.resume_evidence import save_resume_evidence
from services import tailoring_agent
from services.tailoring_agent import MAX_CLAIMS
from routes import tailoring as tailoring_routes


USER = {"username": "tailorroutes", "password": "pw123456"}
OTHER = {"username": "tailorother", "password": "pw123456"}


def login(client, user=USER):
    client.post("/api/auth/signup", json=user)
    assert client.post("/api/auth/login", json=user).status_code == 200
    return client.get("/api/auth/me").get_json()["id"]


def call(name, arguments, call_id="c1"):
    return types.SimpleNamespace(
        id=call_id, function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def response(tool_calls=None, content=None):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def script(monkeypatch, *responses):
    queue = list(responses)
    monkeypatch.setattr(tailoring_agent, "complete", lambda _messages: queue.pop(0))


@pytest.fixture
def setup(client, _db, insert_job):
    user_id = login(client)
    job_id = insert_job(user_id, title="Platform Engineer")
    with _db.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET skills = '["kubernetes", "terraform"]'::jsonb,
                requirements = '[{"skill":"kubernetes","importance":"required","type":"skill"},
                                 {"skill":"terraform","importance":"required","type":"skill"}]'::jsonb
            WHERE id = %s
            """,
            (job_id,),
        )
        save_resume_evidence(cur, user_id, ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", bullets=[
                "Worked on Kubernetes deployments across three regions",
            ])
        ]))
        cur.execute("SELECT id FROM resume_bullets WHERE user_id = %s", (user_id,))
        bullet_id = str(cur.fetchone()[0])
    _db.commit()
    return {"user_id": user_id, "job_id": job_id, "bullet_id": bullet_id}


def grounded_run(monkeypatch, bullet_id):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": bullet_id,
            "proposed_text": "Deployed Kubernetes services across three regions",
            "evidence_bullet_ids": [bullet_id],
        }, "c2")]),
        response([call("flag_gap", {"requirement": "Terraform", "note": "no IaC work found"}, "c3")]),
        response(content="Kubernetes covered; Terraform missing."),
    )


# ── starting a run ───────────────────────────────────────────────────────────

def wait_for_run(client, run_id, timeout=10):
    """The run executes on a worker thread, so poll it the way the UI does."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/tailoring/runs/{run_id}").get_json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("run never finished")


def test_start_returns_immediately_with_a_run_id(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])

    started = client.post(f"/api/jobs/{setup['job_id']}/tailor")

    assert started.status_code == 202
    assert started.get_json()["status"] == "running"
    assert started.get_json()["id"]
    wait_for_run(client, started.get_json()["id"])


def test_run_produces_edits_gaps_and_trace(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])

    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    body = wait_for_run(client, run_id)

    assert body["status"] == "completed"
    assert body["edits"][0]["proposed_text"] == "Deployed Kubernetes services across three regions"
    assert body["edits"][0]["evidence"][0]["bullet_id"] == setup["bullet_id"]
    assert body["gaps"][0]["requirement"] == "terraform"
    # the flag_gap turn is never reached: the edit finished the only candidate, so the run
    # ends there. That refusal is pinned in test_tailoring_agent.py instead.
    assert [t["tool"] for t in body["trace"]] == ["search_resume", "propose_edit"]


def test_detail_answer_resumes_the_same_run(client, monkeypatch, setup):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("request_detail", {
            "requirement": "Kubernetes",
            "bullet_id": setup["bullet_id"],
            "intent": "impact",
            "question": "How much time did this save?",
        }, "c2")]),
    )
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    paused = wait_for_run(client, run_id)
    assert paused["status"] == "waiting_for_user"

    launched = []
    monkeypatch.setattr(tailoring_routes, "_drive_run", lambda *args, **kwargs: launched.append((args, kwargs)))
    question_id = paused["detail_requests"][0]["id"]
    answered = client.patch(
        f"/api/tailoring/questions/{question_id}",
        json={"action": "answer", "answer": "Reduced deployment time by 40%."},
    )

    assert answered.status_code == 202
    deadline = time.time() + 1
    while not launched and time.time() < deadline:
        time.sleep(0.01)
    assert launched[0][0][2] == run_id
    assert launched[0][1]["resume_from"] == 2


def test_detail_answer_validation_and_ownership(client, monkeypatch, setup):
    script(
        monkeypatch,
        response([call("search_resume", {"query": "kubernetes"}, "c1")]),
        response([call("request_detail", {
            "requirement": "Kubernetes", "bullet_id": setup["bullet_id"],
            "intent": "impact", "question": "What was the measurable result?",
        }, "c2")]),
    )
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    question_id = wait_for_run(client, run_id)["detail_requests"][0]["id"]

    assert client.patch(
        f"/api/tailoring/questions/{question_id}", json={"action": "answer", "answer": ""},
    ).status_code == 400
    client.post("/api/auth/logout")
    login(client, OTHER)
    assert client.patch(
        f"/api/tailoring/questions/{question_id}", json={"action": "dismiss"},
    ).status_code == 404


def test_run_on_another_users_job_is_404(client, monkeypatch, setup):
    client.post("/api/auth/logout")
    login(client, OTHER)

    assert client.post(f"/api/jobs/{setup['job_id']}/tailor").status_code == 404


def test_run_without_evidence_is_409(client, monkeypatch, setup, _db):
    with _db.cursor() as cur:
        cur.execute("DELETE FROM resume_bullets WHERE user_id = %s", (setup["user_id"],))
    _db.commit()
    called = []
    monkeypatch.setattr(tailoring_agent, "complete", lambda _m: called.append(1))

    result = client.post(f"/api/jobs/{setup['job_id']}/tailor")

    assert result.status_code == 409
    assert called == []


def test_model_failure_shows_up_on_the_run(client, monkeypatch, setup):
    """The request already returned by the time the model fails, so the failure is
    reported through the run, not the response."""
    def boom(_messages):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(tailoring_agent, "complete", boom)

    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    run = wait_for_run(client, run_id)

    assert run["status"] == "failed"
    assert run["error_code"] == "model_call_failed"


def test_starting_a_run_requires_auth(client, setup):
    client.post("/api/auth/logout")
    assert client.post(f"/api/jobs/{setup['job_id']}/tailor").status_code == 401


# ── reading runs ─────────────────────────────────────────────────────────────

def test_get_run_by_id(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)

    fetched = client.get(f"/api/tailoring/runs/{run_id}")

    assert fetched.status_code == 200
    assert fetched.get_json()["id"] == run_id


def test_a_stranded_run_is_reaped_rather_than_polled_forever(client, monkeypatch, setup, _db):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)

    # A run nothing will pick up, which is no longer the same thing as a silent one: an
    # expired lease just means the next worker claims it. Only exhausted claims are fatal.
    with _db.cursor() as cur:
        cur.execute(
            """
            UPDATE tailoring_runs
            SET status = 'running', completed_at = NULL,
                started_at = now() - interval '1 hour',
                heartbeat_at = now() - interval '1 hour',
                claim_count = %s, lease_expires_at = now() - interval '1 hour'
            WHERE id = %s
            """,
            (MAX_CLAIMS, run_id),
        )
    _db.commit()

    run = client.get(f"/api/tailoring/runs/{run_id}").get_json()

    assert run["status"] == "failed" and run["error_code"] == "abandoned"


def test_a_run_with_a_fresh_heartbeat_is_left_alone(client, monkeypatch, setup, _db):
    """Another worker may still own it — only silence means abandoned."""
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)

    with _db.cursor() as cur:
        cur.execute(
            """
            UPDATE tailoring_runs
            SET status = 'running', completed_at = NULL,
                started_at = now() - interval '1 hour', heartbeat_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )
    _db.commit()

    assert client.get(f"/api/tailoring/runs/{run_id}").get_json()["status"] == "running"


def test_get_run_is_ownership_scoped(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)
    client.post("/api/auth/logout")
    login(client, OTHER)

    assert client.get(f"/api/tailoring/runs/{run_id}").status_code == 404


def test_list_runs_for_a_job(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    wait_for_run(client, client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"])

    listed = client.get(f"/api/jobs/{setup['job_id']}/tailoring").get_json()["runs"]

    assert len(listed) == 1
    assert listed[0]["edit_count"] == 1 and listed[0]["gap_count"] == 1


# ── accepting and rejecting proposals ────────────────────────────────────────

def test_accept_records_the_decision_without_touching_the_bullet(client, monkeypatch, setup, _db):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    edit = wait_for_run(client, run_id)["edits"][0]

    decided = client.patch(f"/api/tailoring/edits/{edit['id']}", json={"status": "accepted"})

    assert decided.status_code == 200
    assert decided.get_json()["status"] == "accepted"
    with _db.cursor() as cur:
        cur.execute("SELECT text FROM resume_bullets WHERE id = %s", (setup["bullet_id"],))
        # the shared evidence is untouched — the tailored wording belongs to this run
        assert cur.fetchone()[0] == "Worked on Kubernetes deployments across three regions"


def test_reject_records_the_decision(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    edit = wait_for_run(client, run_id)["edits"][0]

    assert client.patch(f"/api/tailoring/edits/{edit['id']}", json={"status": "rejected"}).status_code == 200


def test_invalid_decision_is_400(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    edit = wait_for_run(client, run_id)["edits"][0]

    assert client.patch(f"/api/tailoring/edits/{edit['id']}", json={"status": "maybe"}).status_code == 400


def test_cannot_decide_on_someone_elses_edit(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    edit = wait_for_run(client, run_id)["edits"][0]
    client.post("/api/auth/logout")
    login(client, OTHER)

    assert client.patch(f"/api/tailoring/edits/{edit['id']}", json={"status": "accepted"}).status_code == 404


# ── downloading the tailored resume ──────────────────────────────────────────

def accepted_run(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    run = wait_for_run(client, run_id)
    client.patch(f"/api/tailoring/edits/{run['edits'][0]['id']}", json={"status": "accepted"})
    return run_id


def add_ordering_examples(_db, setup):
    """Keep the citable fixture bullet while adding two projects in a deliberate order."""
    with _db.cursor() as cur:
        save_resume_evidence(cur, setup["user_id"], ResumeStructure(entries=[
            ResumeEntryExtraction(kind="experience", organization="Acme", bullets=[
                "Worked on Kubernetes deployments across three regions",
            ]),
            ResumeEntryExtraction(kind="project", title="Issue tracker", bullets=[
                "Managed feature requests in Jira",
            ]),
            ResumeEntryExtraction(kind="project", title="Cluster manager", bullets=[
                "Built a Kubernetes deployment dashboard",
            ]),
        ]))
    _db.commit()


def test_html_download_applies_accepted_edits(client, monkeypatch, setup):
    run_id = accepted_run(client, monkeypatch, setup)

    response = client.get(f"/api/tailoring/runs/{run_id}/resume.html")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Deployed Kubernetes services across three regions" in body
    assert "Worked on Kubernetes deployments across three regions" not in body


def test_latex_download_renders_a_document(client, monkeypatch, setup):
    run_id = accepted_run(client, monkeypatch, setup)

    response = client.get(f"/api/tailoring/runs/{run_id}/resume.tex")

    assert response.status_code == 200
    assert r"\begin{document}" in response.get_data(as_text=True)


def test_downloads_are_attachments_and_never_rendered_inline(client, monkeypatch, setup):
    """The HTML contains user text; serving it inline from our origin would be stored XSS."""
    run_id = accepted_run(client, monkeypatch, setup)

    response = client.get(f"/api/tailoring/runs/{run_id}/resume.html")

    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_download_can_use_original_or_tailored_order(client, monkeypatch, setup, _db):
    add_ordering_examples(_db, setup)
    run_id = accepted_run(client, monkeypatch, setup)

    original = client.get(
        f"/api/tailoring/runs/{run_id}/resume.html?ordering=original"
    ).get_data(as_text=True)
    tailored = client.get(
        f"/api/tailoring/runs/{run_id}/resume.html?ordering=tailored"
    ).get_data(as_text=True)

    assert original.index("Issue tracker") < original.index("Cluster manager")
    assert tailored.index("Cluster manager") < tailored.index("Issue tracker")


def test_tailored_order_is_the_download_default(client, monkeypatch, setup, _db):
    add_ordering_examples(_db, setup)
    run_id = accepted_run(client, monkeypatch, setup)

    body = client.get(f"/api/tailoring/runs/{run_id}/resume.html").get_data(as_text=True)

    assert body.index("Cluster manager") < body.index("Issue tracker")


def test_invalid_download_order_is_400(client, monkeypatch, setup):
    run_id = accepted_run(client, monkeypatch, setup)

    response = client.get(f"/api/tailoring/runs/{run_id}/resume.html?ordering=random")

    assert response.status_code == 400
    assert response.get_json()["error"] == "ordering must be original or tailored"


def test_run_explains_its_composition_plan(client, monkeypatch, setup, _db):
    add_ordering_examples(_db, setup)
    grounded_run(monkeypatch, setup["bullet_id"])

    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    run = wait_for_run(client, run_id)

    assert run["composition"]["changed"] is True
    assert run["composition"]["promoted_projects"] == ["Cluster manager"]


def test_unknown_format_is_400(client, monkeypatch, setup):
    run_id = accepted_run(client, monkeypatch, setup)
    assert client.get(f"/api/tailoring/runs/{run_id}/resume.pdf").status_code in (400, 404)


def test_download_is_ownership_scoped(client, monkeypatch, setup):
    run_id = accepted_run(client, monkeypatch, setup)
    client.post("/api/auth/logout")
    login(client, OTHER)

    assert client.get(f"/api/tailoring/runs/{run_id}/resume.html").status_code == 404


def test_accepting_a_second_rewrite_rejects_the_first(client, monkeypatch, setup, _db):
    """Two accepted edits for one bullet would leave the renderer picking silently."""
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    first = wait_for_run(client, run_id)["edits"][0]

    with _db.cursor() as cur:          # a second proposal for the same bullet
        cur.execute(
            """
            INSERT INTO proposed_edits (run_id, user_id, bullet_id, requirement, proposed_text)
            VALUES (%s, %s, %s, 'Kubernetes', 'A different rewrite') RETURNING id
            """,
            (run_id, setup["user_id"], setup["bullet_id"]),
        )
        second = str(cur.fetchone()[0])
    _db.commit()

    client.patch(f"/api/tailoring/edits/{first['id']}", json={"status": "accepted"})
    client.patch(f"/api/tailoring/edits/{second}", json={"status": "accepted"})

    edits = {e["id"]: e["status"] for e in client.get(f"/api/tailoring/runs/{run_id}").get_json()["edits"]}
    assert edits[second] == "accepted"
    assert edits[first["id"]] == "rejected"


def test_accepting_a_merge_rejects_an_edit_using_its_secondary_bullet(
    client, monkeypatch, setup, _db,
):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)
    with _db.cursor() as cur:
        cur.execute(
            "SELECT entry_id FROM resume_bullets WHERE id = %s", (setup["bullet_id"],),
        )
        entry_id = cur.fetchone()[0]
        extra_text = "Maintained Kubernetes release configurations"
        cur.execute(
            """
            INSERT INTO resume_bullets (entry_id, user_id, text, sort_order, content_hash)
            VALUES (%s, %s, %s, 2, encode(digest(%s, 'sha256'), 'hex')) RETURNING id
            """,
            (entry_id, setup["user_id"], extra_text, extra_text),
        )
        secondary = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO proposed_edits (
                run_id, user_id, bullet_id, requirement, proposed_text, edit_type
            ) VALUES (%s, %s, %s, 'Kubernetes', 'Merged Kubernetes work', 'merge')
            RETURNING id
            """,
            (run_id, setup["user_id"], setup["bullet_id"]),
        )
        merge_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO tailoring_edit_bullets (edit_id, bullet_id, sort_order) VALUES (%s, %s, 1)",
            (merge_id, secondary),
        )
        cur.execute(
            """
            INSERT INTO proposed_edits (
                run_id, user_id, bullet_id, requirement, proposed_text, status
            ) VALUES (%s, %s, %s, 'Kubernetes', 'Other edit', 'accepted')
            RETURNING id
            """,
            (run_id, setup["user_id"], secondary),
        )
        conflicting_id = cur.fetchone()[0]
    _db.commit()

    assert client.patch(
        f"/api/tailoring/edits/{merge_id}", json={"status": "accepted"},
    ).status_code == 200
    with _db.cursor() as cur:
        cur.execute("SELECT status FROM proposed_edits WHERE id = %s", (conflicting_id,))
        assert cur.fetchone()[0] == "rejected"


# ── resuming a stranded run (BUG-098) ───────────────────────────────────────

def strand_run(client, monkeypatch, setup, _db):
    """Run one step, then make it look like the worker was killed by a deploy."""
    script(monkeypatch, response([call("search_resume", {"query": "kubernetes"}, "c1")]))
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)                      # it runs out of scripted turns and fails
    with _db.cursor() as cur:
        cur.execute(
            "UPDATE tailoring_runs SET status = 'failed', error_code = 'abandoned', steps_used = 1 WHERE id = %s",
            (run_id,),
        )
    _db.commit()
    return run_id


def test_resume_picks_the_run_back_up(client, monkeypatch, setup, _db):
    run_id = strand_run(client, monkeypatch, setup, _db)

    script(
        monkeypatch,
        response([call("propose_edit", {
            "requirement": "Kubernetes",
            "bullet_id": setup["bullet_id"],
            "proposed_text": "Deployed Kubernetes services across three regions",
            "evidence_bullet_ids": [setup["bullet_id"]],
        }, "c2")]),
        response(content="Finished after the resume."),
    )
    resumed = client.post(f"/api/tailoring/runs/{run_id}/resume")

    assert resumed.status_code == 202
    assert resumed.get_json()["resumed_from"] == 1

    body = wait_for_run(client, run_id)
    assert body["status"] == "completed"
    # the citation still verifies, because the earlier search is in tool_calls
    assert body["edits"][0]["evidence"][0]["bullet_id"] == setup["bullet_id"]
    assert [t["tool"] for t in body["trace"]] == ["search_resume", "propose_edit"]


def test_resume_refuses_a_finished_run(client, monkeypatch, setup):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)

    refused = client.post(f"/api/tailoring/runs/{run_id}/resume")
    assert refused.status_code == 409
    assert "already finished" in refused.get_json()["error"]


def test_resume_of_another_users_run_is_404(client, monkeypatch, setup, _db):
    run_id = strand_run(client, monkeypatch, setup, _db)

    client.post("/api/auth/logout")
    login(client, OTHER)
    assert client.post(f"/api/tailoring/runs/{run_id}/resume").status_code == 404


def test_resume_of_an_unknown_run_is_404(client, setup):
    assert client.post("/api/tailoring/runs/00000000-0000-0000-0000-000000000000/resume").status_code == 404


def test_a_finished_run_can_be_deleted_with_everything_it_produced(client, monkeypatch, setup, _db):
    grounded_run(monkeypatch, setup["bullet_id"])
    run_id = client.post(f"/api/jobs/{setup['job_id']}/tailor").get_json()["id"]
    wait_for_run(client, run_id)

    assert client.delete(f"/api/tailoring/runs/{run_id}").status_code == 200

    assert client.get(f"/api/tailoring/runs/{run_id}").status_code == 404
    with _db.cursor() as cur:
        # the rows it owned go with it rather than being orphaned
        cur.execute("SELECT count(*) FROM tool_calls WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM proposed_edits WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0


def test_a_run_in_progress_is_not_deletable(client, setup, _db):
    """The rows cascade, so deleting under a live worker leaves it writing to nothing."""
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
            VALUES (%s, %s, 'gpt-4o-mini', 8, 'running') RETURNING id
            """,
            (setup["user_id"], setup["job_id"]),
        )
        run_id = str(cur.fetchone()[0])
    _db.commit()

    response = client.delete(f"/api/tailoring/runs/{run_id}")

    assert response.status_code == 409
    with _db.cursor() as cur:
        cur.execute("SELECT count(*) FROM tailoring_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 1


def test_another_users_run_cannot_be_deleted(client, setup, _db):
    with _db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tailoring_runs (user_id, job_id, model, max_steps, status)
            VALUES (%s, %s, 'gpt-4o-mini', 8, 'completed') RETURNING id
            """,
            (setup["user_id"], setup["job_id"]),
        )
        run_id = str(cur.fetchone()[0])
    _db.commit()

    client.post("/api/auth/logout")
    client.post("/api/auth/signup", json={"username": "runthief", "password": "pw123456"})
    client.post("/api/auth/login", json={"username": "runthief", "password": "pw123456"})

    # not 403: a run they do not own is a run that does not exist, as far as they can tell
    assert client.delete(f"/api/tailoring/runs/{run_id}").status_code == 404
    with _db.cursor() as cur:
        cur.execute("SELECT count(*) FROM tailoring_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == 1
