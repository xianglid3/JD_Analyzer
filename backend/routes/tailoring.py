from flask import Blueprint, Response, jsonify, g

import logging
import re
import threading

from db import get_cursor
from extensions import authenticated_user_key, limiter
from middleware import require_auth
from routes.request_validation import get_json_object
from services.resume_render import build_document, render_html, render_latex
from services.tailoring_agent import (
    DEFAULT_MAX_STEPS,
    execute_run,
    load_run,
    reap_abandoned_run,
    start_run,
)


tailoring_bp = Blueprint("tailoring", __name__, url_prefix="/api")
logger = logging.getLogger(__name__)

VALID_EDIT_DECISIONS = {"accepted", "rejected"}


@tailoring_bp.route("/jobs/<job_id>/tailor", methods=["POST"])
@require_auth
@limiter.limit("3 per minute; 20 per day", key_func=authenticated_user_key)
def start_tailoring_run(job_id):
    """Start a run and hand back its id. The work happens on a worker thread and writes as it
    goes, so the client can poll and show what's actually happening."""
    started = start_run(get_cursor, g.user_id, job_id, max_steps=DEFAULT_MAX_STEPS)

    if started is None:
        return jsonify({"error": "job not found"}), 404
    if isinstance(started, dict) and started.get("error") == "no_evidence":
        return jsonify({
            "error": "no resume evidence yet — build your structured resume first",
        }), 409
    if isinstance(started, dict) and started.get("error") == "stale_evidence":
        return jsonify({
            "error": "your resume changed after this experience was extracted — re-extract it first",
        }), 409
    if isinstance(started, dict) and started.get("error") == "quota_exceeded":
        return jsonify({"error": f"daily AI budget reached ({started['detail']})"}), 429

    user_id, run_id = g.user_id, started
    worker = threading.Thread(
        target=_drive_run,
        args=(user_id, job_id, run_id),
        name=f"tailoring-{run_id}",
        daemon=True,
    )
    worker.start()

    return jsonify({"id": str(run_id), "status": "running"}), 202


def _drive_run(user_id, job_id, run_id):
    """The worker. Touches no request state — the ids were read before the thread started."""
    try:
        execute_run(get_cursor, user_id, job_id, run_id, max_steps=DEFAULT_MAX_STEPS)
    except Exception:
        logger.exception("tailoring worker crashed run_id=%s", run_id)


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
                   count(DISTINCT e.id), count(DISTINCT gp.id)
            FROM tailoring_runs AS r
            LEFT JOIN proposed_edits AS e ON e.run_id = r.id
            LEFT JOIN gaps AS gp ON gp.run_id = r.id
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

        # one accepted rewrite per bullet: two would leave the renderer choosing silently,
        # so accepting this one rejects the other in the same transaction
        if status == "accepted" and target[1] is not None:
            cur.execute(
                """
                UPDATE proposed_edits
                SET status = 'rejected'
                WHERE run_id = %s AND bullet_id = %s AND user_id = %s
                  AND id <> %s AND status = 'accepted'
                """,
                (target[0], target[1], g.user_id, edit_id),
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

    if row is None:
        return jsonify({"error": "proposed edit not found"}), 404
    return jsonify({"id": str(row[0]), "status": row[1]}), 200


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

    with get_cursor() as cur:
        run = load_run(cur, g.user_id, run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        document = build_document(cur, g.user_id, run_id)

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
