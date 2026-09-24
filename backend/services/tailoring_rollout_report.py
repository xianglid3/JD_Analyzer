"""Operational evidence for deciding whether question-first V2 may advance or roll back.

The report groups stored runs by their pinned contract. It intentionally reports behavior rather
than a synthetic quality score: questions, skips, answer-to-edit conversion, validator outcomes,
retries, latency, cost, and user decisions. A human still has to read sampled questions and edits.
"""

from collections import defaultdict


RESUMABLE_ERRORS = {"abandoned", "tool_execution_failed", "model_call_failed", "stopped_early"}


def _empty(version):
    return {
        "contract": version,
        "runs": 0,
        "statuses": {},
        "questions": {"filed": 0, "answered": 0, "dismissed": 0},
        "answer_to_edit": {"edited_bullets": 0, "answered_bullets": 0},
        "reviews_unavailable": 0,
        "coordinator_selected": 0,
        "validation": {"hard_blocks": 0, "repair_requests": 0, "warned_edits": 0},
        "candidates": {"attempts": 0, "needs_review": 0},
        "edits": {"proposed": 0, "accepted": 0, "rejected": 0},
        "model": {"calls": 0, "latency_ms": 0, "cost_usd": 0.0},
        "resumable_runs": 0,
    }


def report(cur, days=7):
    cur.execute(
        """
        SELECT r.id, r.status, r.error_code, r.steps_used, r.max_steps,
               coalesce(marker.arguments ->> 'version', 'v1') AS contract
        FROM tailoring_runs AS r
        LEFT JOIN tool_calls AS marker
          ON marker.run_id = r.id AND marker.call_id = 'tailoring_review_contract'
        WHERE r.started_at >= now() - make_interval(days => %s)
        ORDER BY r.started_at
        """,
        (days,),
    )
    runs = cur.fetchall()
    grouped = defaultdict(list)
    output = {}
    for run_id, status, error_code, steps_used, max_steps, version in runs:
        version = version if version in {"v1", "v2"} else "v1"
        grouped[version].append(run_id)
        row = output.setdefault(version, _empty(version))
        row["runs"] += 1
        row["statuses"][status] = row["statuses"].get(status, 0) + 1
        if (
            status in {"running", "waiting_for_user"}
            or (
                status in {"failed", "incomplete"}
                and error_code in RESUMABLE_ERRORS
                and steps_used < max_steps
            )
        ):
            row["resumable_runs"] += 1

    for version, run_ids in grouped.items():
        row = output[version]
        cur.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE status = 'answered'),
                   count(*) FILTER (WHERE status = 'dismissed'),
                   count(DISTINCT bullet_id) FILTER (WHERE status = 'answered'),
                   count(DISTINCT q.bullet_id) FILTER (WHERE d.edit_id IS NOT NULL)
            FROM tailoring_detail_requests AS q
            LEFT JOIN tailoring_edit_details AS d ON d.detail_request_id = q.id
            WHERE q.run_id = ANY(%s::uuid[])
            """,
            (run_ids,),
        )
        filed, answered, dismissed, answered_bullets, edited_bullets = cur.fetchone()
        row["questions"] = {
            "filed": filed, "answered": answered, "dismissed": dismissed,
        }
        row["answer_to_edit"] = {
            "edited_bullets": edited_bullets, "answered_bullets": answered_bullets,
        }

        cur.execute(
            """
            SELECT coalesce(sum(attempts), 0),
                   count(*) FILTER (WHERE status = 'needs_review')
            FROM tailoring_candidates WHERE run_id = ANY(%s::uuid[])
            """,
            (run_ids,),
        )
        attempts, needs_review = cur.fetchone()
        row["candidates"] = {"attempts": int(attempts), "needs_review": needs_review}

        cur.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'proposed'),
                   count(*) FILTER (WHERE status = 'accepted'),
                   count(*) FILTER (WHERE status = 'rejected')
            FROM proposed_edits WHERE run_id = ANY(%s::uuid[])
            """,
            (run_ids,),
        )
        proposed, accepted, rejected = cur.fetchone()
        row["edits"] = {"proposed": proposed, "accepted": accepted, "rejected": rejected}

        cur.execute(
            """
            SELECT count(*), coalesce(sum(latency_ms), 0), coalesce(sum(cost_usd), 0)
            FROM llm_calls WHERE run_id = ANY(%s::uuid[])
            """,
            (run_ids,),
        )
        calls, latency, cost = cur.fetchone()
        row["model"] = {
            "calls": calls, "latency_ms": int(latency), "cost_usd": float(cost),
        }

        cur.execute(
            """
            SELECT tool_name, result, status
            FROM tool_calls
            WHERE run_id = ANY(%s::uuid[])
              AND tool_name IN ('bullet_review', 'question_coordinator', 'propose_edit')
            """,
            (run_ids,),
        )
        for tool, result, status in cur.fetchall():
            result = result or {}
            if tool == "bullet_review":
                reviews = result.get("reviews") or {}
                row["reviews_unavailable"] += sum(
                    1 for review in reviews.values()
                    if (review or {}).get("decision") == "REVIEW_UNAVAILABLE"
                )
            elif tool == "question_coordinator" and status == "completed":
                row["coordinator_selected"] += len(result.get("selected_ids") or [])
            elif tool == "propose_edit":
                validation = result.get("validation") or {}
                if status == "failed":
                    if validation.get("hard_blocks"):
                        row["validation"]["hard_blocks"] += 1
                    elif validation.get("repair_requests"):
                        row["validation"]["repair_requests"] += 1
                elif result.get("validation_warnings"):
                    row["validation"]["warned_edits"] += 1

    return {"days": days, "contracts": output}
