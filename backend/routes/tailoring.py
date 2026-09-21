from flask import Blueprint, Response, jsonify, g, request

import logging
import os
import re
from uuid import UUID
import threading

from config import env_flag
from db import get_cursor
from services.resume_evidence import stale_edit_ids
from services.surface_skill import SurfaceRefused, surface_skill
from services.usage import QuotaExceeded
from extensions import authenticated_user_key, limiter
from middleware import require_auth
from routes.request_validation import get_json_object
from services.resume_render import build_document, render_html, render_latex
from services.tailoring_agent import (
    DEFAULT_MAX_STEPS,
    claim_run,
    execute_run,
    load_job_context,
    load_run,
    reap_abandoned_run,
    resolve_detail_request,
    resume_run,
    start_run,
)


tailoring_bp = Blueprint("tailoring", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)


def inline_worker():
    """Whether this process drives runs itself.

    Production runs `flask --app app tailoring-worker` as its own process, so a deploy cannot
    take a run down with the web server. That would mean a second terminal for every local
    session, so TAILORING_INLINE=1 drives the run in a thread here instead — same code, same
    lease, same fencing token; only the process differs. Read per call, not at import, so a
    test can turn it on.
    """
    return env_flag("TAILORING_INLINE")


VALID_EDIT_DECISIONS = {"accepted", "rejected"}
MAX_DETAIL_ANSWER_CHARS = 500


class StaleEvidence(Exception):
    """Raised inside the accept transaction so the UPDATE rolls back with it."""


@tailoring_bp.route("/jobs/<job_id>/tailor", methods=["POST"])
@require_auth
@limiter.limit("3 per minute; 20 per day", key_func=authenticated_user_key)
def start_tailoring_run(job_id):
    """Start a run and hand back its id. The work happens on a worker thread and writes as it
    goes, so the client can poll and show what's actually happening."""
    started = start_run(get_cursor, g.user_id, job_id, max_steps=DEFAULT_MAX_STEPS)

    if started is None:
        return jsonify({"error": "job not found"}), 404
    # Extraction is automatic after upload, but the user must confirm the draft before it is
    # trusted as tailoring evidence. The reason code lets the client link to that review.
    if isinstance(started, dict) and started.get("error") == "no_evidence":
        return jsonify({
            "error": "review and confirm your resume experience before tailoring",
            "reason": "needs_confirmation",
        }), 409
    if isinstance(started, dict) and started.get("error") == "stale_evidence":
        return jsonify({
            "error": "your resume changed — re-read and confirm its experience before tailoring",
            "reason": "needs_confirmation",
        }), 409
    if isinstance(started, dict) and started.get("error") == "quota_exceeded":
        return jsonify({"error": f"daily AI budget reached ({started['detail']})"}), 429

    if isinstance(started, dict) and started.get("error") == "start_conflict":
        return jsonify({"error": "a tailoring run for this job is already starting"}), 409

    run_id = started["run_id"]
    if started["created"]:
        _hand_to_worker(g.user_id, job_id, run_id)
    # A repeat of a request that is already running is that request, not a new one. Returning
    # the run that exists is what a double-clicked button and a retried POST both want.
    return jsonify({"id": str(run_id), "status": "running"}), 202 if started["created"] else 200


def _hand_to_worker(user_id, job_id, run_id, resume_from=0):
    """Make the run available to a worker.

    In production that is all this does: the row is `running` with no lease, so the worker
    process claims it on its next poll. Inline mode claims it here and drives it in a thread.
    """
    if not inline_worker():
        return
    with get_cursor(commit=True) as cur:
        claimed = claim_run(cur)
    if claimed is None or str(claimed["run_id"]) != str(run_id):
        # somebody else took it, or it is not claimable — either way it is not ours to drive
        return
    threading.Thread(
        target=_drive_run,
        args=(user_id, job_id, run_id),
        kwargs={"resume_from": resume_from, "token": claimed["token"]},
        name=f"tailoring-{run_id}",
        daemon=True,
    ).start()


def _drive_run(user_id, job_id, run_id, resume_from=0, token=None):
    """The inline worker. Touches no request state — the ids were read before the thread
    started."""
    try:
        execute_run(get_cursor, user_id, job_id, run_id,
                    max_steps=DEFAULT_MAX_STEPS, resume_from=resume_from, token=token)
    except Exception:
        logger.exception("tailoring worker crashed run_id=%s", run_id)


RESUME_REFUSALS = {
    "still_running": ("a worker is still driving this run", 409),
    "awaiting_input": ("answer or skip the pending question before resuming", 409),
    "already_finished": ("this run already finished", 409),
    "not_resumable": ("this run did not stop in a resumable state", 409),
}


@tailoring_bp.route("/tailoring/runs/<run_id>/resume", methods=["POST"])
@require_auth
@limiter.limit("6 per minute; 40 per day", key_func=authenticated_user_key)
def resume_tailoring_run(run_id):
    """Pick a stranded run back up from the step it reached.

    A deploy kills the worker thread mid-run. Every step is already on disk, so the model
    calls that were paid for are not repeated — the conversation is rebuilt from `tool_calls`
    and the loop carries on from `steps_used + 1`.
    """
    outcome = resume_run(get_cursor, g.user_id, run_id)

    if outcome is None:
        return jsonify({"error": "run not found"}), 404
    if isinstance(outcome, dict):
        if outcome["error"] == "quota_exceeded":
            return jsonify({"error": f"daily AI budget reached ({outcome['detail']})"}), 429
        message, code = RESUME_REFUSALS[outcome["error"]]
        return jsonify({"error": message}), code

    job_id, steps_used = outcome
    _hand_to_worker(g.user_id, job_id, run_id, resume_from=steps_used)

    return jsonify({"id": str(run_id), "status": "running", "resumed_from": steps_used}), 202


@tailoring_bp.route("/tailoring/runs/<run_id>", methods=["GET"])
@require_auth
def get_tailoring_run(run_id):
    with get_cursor(commit=True) as cur:
        reap_abandoned_run(cur, g.user_id, run_id)
        run = load_run(cur, g.user_id, run_id)

    if run is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run), 200


@tailoring_bp.route("/jobs/<job_id>/tailoring", methods=["GET"])
@require_auth
def list_job_tailoring_runs(job_id):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.status, r.steps_used, r.started_at, r.completed_at,
                   count(DISTINCT e.id), r.gap_count
            FROM tailoring_runs AS r
            LEFT JOIN proposed_edits AS e ON e.run_id = r.id
            WHERE r.job_id = %s AND r.user_id = %s
            GROUP BY r.id
            ORDER BY r.started_at DESC
            LIMIT 20
            """,
            (job_id, g.user_id),
        )
        runs = [
            {
                "id": str(row[0]),
                "status": row[1],
                "steps_used": row[2],
                "started_at": row[3].isoformat(),
                "completed_at": row[4].isoformat() if row[4] else None,
                "edit_count": row[5],
                "gap_count": row[6],
            }
            for row in cur.fetchall()
        ]

    return jsonify({"runs": runs}), 200


@tailoring_bp.route("/tailoring/edits/<edit_id>", methods=["PATCH"])
@require_auth
def decide_proposed_edit(edit_id):
    try:
        return _decide_proposed_edit(edit_id)
    except StaleEvidence:
        return jsonify({
            "error": "the evidence behind this edit changed after it was proposed — "
                     "run tailoring again against your current resume",
        }), 409


def _decide_proposed_edit(edit_id):
    """Accept or reject a proposal. Accepting records the decision without touching the
    bullet — the wording is for this job, the bullet is shared by all of them."""
    data, error = get_json_object()
    if error:
        return error

    status = data.get("status")
    if status not in VALID_EDIT_DECISIONS:
        return jsonify({"error": "status must be accepted or rejected"}), 400

    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT run_id, bullet_id FROM proposed_edits WHERE id = %s AND user_id = %s FOR UPDATE",
            (edit_id, g.user_id),
        )
        target = cur.fetchone()
        if target is None:
            return jsonify({"error": "proposed edit not found"}), 404

        # Serialize every decision in this run. The partial unique index only covers each
        # edit's primary bullet; a merge also consumes secondary bullets.
        cur.execute("SELECT id FROM tailoring_runs WHERE id = %s FOR UPDATE", (target[0],))

        # one accepted rewrite per bullet: two would leave the renderer choosing silently,
        # so accepting this one rejects the other in the same transaction
        if status == "accepted" and target[1] is not None:
            cur.execute(
                """
                WITH target_bullets AS (
                    SELECT bullet_id FROM proposed_edits WHERE id = %s
                    UNION
                    SELECT bullet_id FROM tailoring_edit_bullets WHERE edit_id = %s
                ), conflicting_edits AS (
                    SELECT candidate.id
                    FROM proposed_edits AS candidate
                    WHERE candidate.run_id = %s AND candidate.user_id = %s
                      AND candidate.id <> %s AND candidate.status = 'accepted'
                      AND (
                          candidate.bullet_id IN (SELECT bullet_id FROM target_bullets)
                          OR EXISTS (
                              SELECT 1 FROM tailoring_edit_bullets AS source
                              WHERE source.edit_id = candidate.id
                                AND source.bullet_id IN (SELECT bullet_id FROM target_bullets)
                          )
                      )
                )
                UPDATE proposed_edits
                SET status = 'rejected'
                WHERE id IN (SELECT id FROM conflicting_edits)
                """,
                (edit_id, edit_id, target[0], g.user_id, edit_id),
            )

        cur.execute(
            """
            UPDATE proposed_edits
            SET status = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, status
            """,
            (status, edit_id, g.user_id),
        )
        row = cur.fetchone()

        # Accepting is the moment a proposal becomes the user's own claim, so the evidence
        # behind it has to still say what it said when the model read it. Bullet ids survive
        # a reword on purpose, so nothing else would notice (AE-01).
        if status == "accepted" and row is not None:
            if stale_edit_ids(cur, g.user_id, edit_id=edit_id):
                raise StaleEvidence()

    if row is None:
        return jsonify({"error": "proposed edit not found"}), 404
    return jsonify({"id": str(row[0]), "status": row[1]}), 200


@tailoring_bp.route("/tailoring/runs/<run_id>", methods=["DELETE"])
@require_auth
def delete_tailoring_run(run_id):
    """Remove a run and everything it produced.

    Refused while a worker owns it: the rows cascade, so deleting underneath a running worker
    would have it writing tool calls and edits against a run that no longer exists. Finish or
    abandon it first.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT status FROM tailoring_runs WHERE id = %s AND user_id = %s",
            (run_id, g.user_id),
        )
        row = cur.fetchone()
        if row is None:
            return jsonify({"error": "run not found"}), 404
        if row[0] in ("running", "waiting_for_user"):
            return jsonify({"error": "this run is still in progress"}), 409

        # tool_calls, proposed_edits, evidence_links, candidates and questions all cascade
        cur.execute(
            "DELETE FROM tailoring_runs WHERE id = %s AND user_id = %s RETURNING id",
            (run_id, g.user_id),
        )
        deleted = cur.fetchone()

    return jsonify({"deleted": str(deleted[0])}), 200


@tailoring_bp.route("/tailoring/runs/<run_id>/surface", methods=["POST"])
@require_auth
@limiter.limit("10 per minute; 60 per day", key_func=authenticated_user_key)
def surface_skill_into_run(run_id):
    """"I used this on that project" — recorded, and turned into a proposed edit here.

    Rate-limited like the other paid actions: it makes one model call, and a user clicking
    through every gap on a posting should cost a bounded number of them.
    """
    data, error = get_json_object()
    if error:
        return error

    entry_id = data.get("entry_id")
    try:
        entry_id = str(UUID(str(entry_id)))
    except (TypeError, ValueError, AttributeError):
        return jsonify({"error": "entry_id must be a valid id"}), 400

    try:
        with get_cursor(commit=True) as cur:
            outcome = surface_skill(
                cur, g.user_id, run_id,
                skill=data.get("skill"), entry_id=entry_id, detail=data.get("detail"),
            )
    except SurfaceRefused as exc:
        # 422: the request was understood and the claim could not be supported. The message is
        # the useful part — usually that the answer does not carry the fact the rewrite needs.
        return jsonify({"error": str(exc)}), 422
    except QuotaExceeded as exc:
        return jsonify({"error": f"daily AI budget reached ({exc})"}), 429

    if outcome is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(outcome), 201


@tailoring_bp.route("/tailoring/questions/<question_id>", methods=["PATCH"])
@require_auth
@limiter.limit("12 per minute; 100 per day", key_func=authenticated_user_key)
def resolve_tailoring_question(question_id):
    """Record a fact the user supplied, or let them skip it, then continue the same run."""
    data, error = get_json_object()
    if error:
        return error

    action = data.get("action", "answer")
    if action not in {"answer", "dismiss"}:
        return jsonify({"error": "action must be answer or dismiss"}), 400
    # For a "did you use this here?" question the yes/no is recorded, not inferred from the
    # prose — "I didn't use Redis" names Redis and would otherwise read as confirmation.
    used = data.get("used")
    if used is not None and not isinstance(used, bool):
        return jsonify({"error": "used must be true or false"}), 400
    answer = data.get("answer")
    if action == "answer":
        if not isinstance(answer, str) or not answer.strip():
            return jsonify({"error": "answer is required"}), 400
        answer = answer.strip()
        if len(answer) > MAX_DETAIL_ANSWER_CHARS:
            return jsonify({"error": f"answer must be {MAX_DETAIL_ANSWER_CHARS} characters or fewer"}), 400
        if any(ord(character) < 32 and character not in "\n\t" for character in answer):
            return jsonify({"error": "answer contains invalid control characters"}), 400

    outcome = resolve_detail_request(
        get_cursor, g.user_id, question_id,
        answer=answer if action == "answer" else None,
        dismiss=action == "dismiss",
        used=used,
    )
    if outcome is None:
        return jsonify({"error": "question not found"}), 404
    if outcome.get("error") == "already_resolved":
        return jsonify({"error": "question was already answered or skipped"}), 409
    if outcome.get("error") == "run_not_waiting":
        return jsonify({"error": "tailoring run is not waiting for an answer"}), 409

    if outcome.get("resume"):
        _hand_to_worker(g.user_id, outcome["job_id"], outcome["run_id"],
                        resume_from=outcome["steps_used"])
        return jsonify({"run_id": outcome["run_id"], "status": "running"}), 202
    return jsonify({"run_id": outcome["run_id"], "status": outcome["status"]}), 200


FORMATS = {
    "html": ("text/html; charset=utf-8", "html"),
    "tex": ("application/x-tex; charset=utf-8", "tex"),
}


def _filename(header, run_id, extension):
    name = (header.get("full_name") or "resume").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "resume"
    return f"{slug}-tailored-{str(run_id)[:8]}.{extension}"


@tailoring_bp.route("/tailoring/runs/<run_id>/resume.<fmt>", methods=["GET"])
@require_auth
def download_tailored_resume(run_id, fmt):
    """The tailored resume, rendered from the accepted edits. Always a download — the HTML
    holds user text, and serving it inline from our origin would be stored XSS."""
    if fmt not in FORMATS:
        return jsonify({"error": "format must be html or tex"}), 400
    ordering = request.args.get("ordering", "tailored")
    if ordering not in {"original", "tailored"}:
        return jsonify({"error": "ordering must be original or tailored"}), 400

    with get_cursor() as cur:
        run = load_run(cur, g.user_id, run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        # Raw requirements rescore every source bullet. The public fit explanation intentionally
        # shows only three citations, so it is too small to drive a complete ordering plan.
        _job, _assessment, requirements = load_job_context(cur, g.user_id, run["job_id"])
        document = build_document(
            cur,
            g.user_id,
            run_id,
            requirements=requirements if ordering == "tailored" else None,
        )

    if not document["sections"] and not document["skills"]:
        return jsonify({"error": "no structured resume to render — build one first"}), 409

    content_type, extension = FORMATS[fmt]
    body = render_html(document) if fmt == "html" else render_latex(document)

    response = Response(body, content_type=content_type)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{_filename(document["header"], run_id, extension)}"'
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response
