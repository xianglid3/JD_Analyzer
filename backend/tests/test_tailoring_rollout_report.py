"""The rollout report must describe stored V1/V2 behavior, not infer it from flags."""

import json

from services.tailoring_rollout_report import report


def test_empty_rollout_report(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, jobs, users CASCADE")
        data = report(cur, days=7)

    assert data == {"days": 7, "contracts": {}}


def test_v2_rollout_metrics_come_from_pinned_run_records(_db):
    with _db.cursor() as cur:
        cur.execute("TRUNCATE tailoring_runs, jobs, users CASCADE")
        cur.execute("INSERT INTO users (username, password_hash) VALUES ('rollout', 'x') RETURNING id")
        user_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO jobs (user_id, raw_description) VALUES (%s, 'posting') RETURNING id",
            (user_id,),
        )
        job_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO resume_entries (user_id, kind, title) "
            "VALUES (%s, 'project', 'Worker') RETURNING id",
            (user_id,),
        )
        entry_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO resume_bullets (entry_id, user_id, text, content_hash)
            VALUES (%s, %s, 'Worked on the worker.', 'rollout-worker') RETURNING id
            """,
            (entry_id, user_id),
        )
        bullet_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO tailoring_runs (
                user_id, job_id, status, model, max_steps, steps_used, completed_at
            ) VALUES (%s, %s, 'completed', 'gpt-4o-mini', 12, 2, now()) RETURNING id
            """,
            (user_id, job_id),
        )
        run_id = cur.fetchone()[0]
        tool_rows = [
            ("tailoring_review_contract", "tailoring_review_contract", {"version": "v2"}, {}),
            ("bullet_review:b1", "bullet_review", {}, {
                "reviews": {"b1": {"decision": "REVIEW_UNAVAILABLE"}},
            }),
            ("question_coordinator:v2", "question_coordinator", {}, {
                "selected_ids": ["b1:q1", "b1:q2"], "rejected": [],
            }),
            ("repair", "propose_edit", {}, {"validation": {
                "hard_blocks": [], "repair_requests": [{"code": "possible_omission"}],
            }}),
            ("edit", "propose_edit", {}, {
                "edit_id": "00000000-0000-4000-8000-000000000001",
                "validation_warnings": [{"code": "possible_omission"}],
            }),
        ]
        for index, (call_id, tool, arguments, result) in enumerate(tool_rows, start=1):
            cur.execute(
                """
                INSERT INTO tool_calls (
                    run_id, step_number, call_id, tool_name, arguments, result, status,
                    error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, min(index, 20), call_id, tool, json.dumps(arguments),
                    json.dumps(result), "failed" if call_id == "repair" else "completed",
                    "rewrite needs one repair" if call_id == "repair" else None,
                ),
            )
        cur.execute(
            """
            INSERT INTO tailoring_detail_requests (
                run_id, user_id, bullet_id, requirement, question, answer, status, resolved_at
            ) VALUES (%s, %s, %s, NULL, 'What did you build?', 'Built the worker.', 'answered', now())
            RETURNING id
            """,
            (run_id, user_id, bullet_id),
        )
        question_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO proposed_edits (
                run_id, user_id, bullet_id, requirement, proposed_text, status
            ) VALUES (%s, %s, %s, NULL, 'Built the worker.', 'accepted') RETURNING id
            """,
            (run_id, user_id, bullet_id),
        )
        edit_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO tailoring_edit_details (edit_id, detail_request_id) VALUES (%s, %s)",
            (edit_id, question_id),
        )
        cur.execute(
            """
            INSERT INTO tailoring_candidates (
                run_id, user_id, position, bullet_id, action, status, attempts
            ) VALUES (%s, %s, 0, %s, 'ask', 'handled', 2)
            """,
            (run_id, user_id, bullet_id),
        )
        cur.execute(
            """
            INSERT INTO llm_calls (
                user_id, kind, run_id, model, prompt_tokens, completion_tokens,
                cost_usd, latency_ms
            ) VALUES (%s, 'tailoring_step', %s, 'gpt-4o-mini', 100, 20, 0.001, 250)
            """,
            (user_id, run_id),
        )

        data = report(cur, days=7)["contracts"]["v2"]

    assert data["runs"] == 1
    assert data["questions"] == {"filed": 1, "answered": 1, "dismissed": 0}
    assert data["answer_to_edit"] == {"edited_bullets": 1, "answered_bullets": 1}
    assert data["reviews_unavailable"] == 1
    assert data["coordinator_selected"] == 2
    assert data["validation"] == {
        "hard_blocks": 0, "repair_requests": 1, "warned_edits": 1,
    }
    assert data["candidates"] == {"attempts": 2, "needs_review": 0}
    assert data["edits"]["accepted"] == 1
    assert data["model"] == {"calls": 1, "latency_ms": 250, "cost_usd": 0.001}
