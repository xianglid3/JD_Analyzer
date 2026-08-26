from flask import Blueprint, request, jsonify, g
import json
import logging
import hashlib
from datetime import date
from uuid import UUID
from db import get_cursor
from middleware import require_auth
from extensions import authenticated_user_key, limiter
from services.jd_preprocess import preprocess_text
from services.openai_services import analyze_job_description
from services.match import compute_match
from routes.analysis_errors import analysis_error_response
from routes.request_validation import get_json_object

jobs_bp = Blueprint("jobs", __name__, url_prefix = "/api/jobs")
logger = logging.getLogger(__name__)
#accetpable status
VALID_STATUSES = {"saved", "applied", "interview", "offer", "rejected", "ghosted", "accepted", "decline"}
IDEMPOTENCY_STALE_AFTER = "10 minutes"
IDEMPOTENCY_COMPLETED_TTL = "7 days"
MAX_NOTES_CHARS = 5000


def release_idempotency_key(user_id, idempotency_key, request_hash):
    """Allow a safe retry when analysis or persistence fails."""
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                """
                DELETE FROM idempotency_requests
                WHERE user_id = %s
                  AND idempotency_key = %s
                  AND request_hash = %s
                  AND job_id IS NULL
                """,
                (user_id, idempotency_key, request_hash),
            )
    except Exception:
        logger.exception("failed to release idempotency key")


def create_job_payload(row, replayed=False):
    (job_id, title, summary, no_bs_translation, skills,
     company_name, location, work_type, created_at) = row
    payload = {
        "id": str(job_id),
        "title": title,
        "summary": summary,
        "no_bs_translation": no_bs_translation,
        "skills": skills,
        "company_name": company_name,
        "location": location,
        "work_type": work_type,
        "created_at": created_at.isoformat(),
    }
    if replayed:
        payload["replayed"] = True
    return payload


@jobs_bp.route("", methods = ["POST"])
@require_auth
@limiter.limit("5 per minute; 50 per day", key_func=authenticated_user_key)
def create_job():
    data, error = get_json_object()
    if error:
        return error

    description = data.get("description")
    if not isinstance(description, str):
        return jsonify({"error": "Description must be text"}), 400
    raw_description = description.strip()

    #validation length
    if len(raw_description) < 50:
        return jsonify({"error": "Description too short"}), 400
    if len(raw_description) > 10000:
        return jsonify({"error": "Description too long"}), 400

    #job idempotency stuff
    idempotency_key = request.headers.get("Idempotency-Key", "")
    try:
        UUID(idempotency_key)
    except ValueError:
        return jsonify({"error": "Valid Idempotency-Key required"}), 400

    request_hash = hashlib.sha256(
        raw_description.encode("utf-8")
    ).hexdigest()

    # Opportunistically bound this user's reservation history, then atomically
    # claim the new key before spending money on an OpenAI call.
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            DELETE FROM idempotency_requests
            WHERE user_id = %s
              AND (
                  (job_id IS NULL AND created_at < now() - %s::interval)
                  OR
                  (job_id IS NOT NULL AND created_at < now() - %s::interval)
              )
            """,
            (g.user_id, IDEMPOTENCY_STALE_AFTER, IDEMPOTENCY_COMPLETED_TTL),
        )
        cur.execute(
            """
            INSERT INTO idempotency_requests
                (user_id, idempotency_key, request_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, idempotency_key) DO NOTHING
            RETURNING idempotency_key
            """,
            (g.user_id, idempotency_key, request_hash),
        )
        reserved = cur.fetchone()

        existing = None
        if reserved is None:
            cur.execute(
                """
                SELECT r.request_hash, j.id, j.title, j.summary,
                       j.no_bs_translation, j.skills, j.company_name,
                       j.location, j.work_type, j.created_at
                FROM idempotency_requests AS r
                LEFT JOIN jobs AS j ON j.id = r.job_id
                WHERE r.user_id = %s AND r.idempotency_key = %s
                """,
                (g.user_id, idempotency_key),
            )
            existing = cur.fetchone()

    if reserved is None:
        if existing is None:
            response = jsonify({"error": "Analysis already in progress, please retry shortly"})
            response.headers["Retry-After"] = "3"
            return response, 409
        if existing[0] != request_hash:
            return jsonify({
                "error": "Idempotency-Key was already used with a different description"
            }), 409
        if existing[1] is None:
            response = jsonify({"error": "Analysis already in progress, please retry shortly"})
            response.headers["Retry-After"] = "3"
            return response, 409
        return jsonify(create_job_payload(existing[1:], replayed=True)), 200

    cleaned_description = preprocess_text(raw_description)

    # AI call stays OUTSIDE the db connection — don't hold a connection open for 3-8s
    try:
        job = analyze_job_description(cleaned_description)
    except Exception as exc:
        release_idempotency_key(g.user_id, idempotency_key, request_hash)
        return analysis_error_response(exc, logger)

    try:
        with get_cursor(commit=True) as cur:
            cur.execute("SELECT skills FROM resumes WHERE user_id = %s", (g.user_id,))
            resume_row = cur.fetchone()
            resume_skills = resume_row[0] if resume_row else []
            match = compute_match(job.skills, resume_skills)
            match_score = match["score"] if match else None
            match_detail = {
                "matched": match["matched"],
                "missing": match["missing"],
            } if match else None

            cur.execute(
                """
                INSERT INTO jobs (user_id, raw_description, title, summary, no_bs_translation,
                                  skills, company_name, location, work_type, match_score, match_detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    g.user_id, raw_description, job.title, job.summary, job.no_bs_translation,
                    json.dumps(job.skills), job.company_name, job.location, job.work_type,
                    match_score, json.dumps(match_detail) if match_detail else None,
                ),
            )
            new_id, created_at = cur.fetchone()

            cur.execute(
                """
                UPDATE idempotency_requests
                SET job_id = %s
                WHERE user_id = %s
                  AND idempotency_key = %s
                  AND request_hash = %s
                  AND job_id IS NULL
                """,
                (new_id, g.user_id, idempotency_key, request_hash),
            )
            if cur.rowcount != 1:
                raise RuntimeError("idempotency reservation disappeared")
    except Exception:
        release_idempotency_key(g.user_id, idempotency_key, request_hash)
        raise

    return jsonify(create_job_payload((
        new_id, job.title, job.summary, job.no_bs_translation, job.skills,
        job.company_name, job.location, job.work_type, created_at,
    ))), 201


#list all of the saved jobs from a user
@jobs_bp.route("", methods = ["GET"])
@require_auth
def list_jobs():
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "page must be a positive integer"}), 400

    if page < 1:
        return jsonify({"error": "page must be a positive integer"}), 400

    per_page = 20
    offset = (page - 1) * per_page

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    sort_key = request.args.get("sort", "created_at")
    direction = request.args.get("direction", "desc").lower()

    if status and status not in VALID_STATUSES:
        return jsonify({"error": "invalid status"}), 400

    sort_columns = {
        "title": "title",
        "company_name": "company_name",
        "location": "location",
        "work_type": "work_type",
        "match_score": "match_score",
        "status": "status",
        "created_at": "created_at",
    }
    if sort_key not in sort_columns or direction not in {"asc", "desc"}:
        return jsonify({"error": "invalid sort"}), 400

    where_parts = ["user_id = %s"]
    params = [g.user_id]

    if search:
        where_parts.append("(title ILIKE %s OR company_name ILIKE %s)")
        pattern = f"%{search}%"
        params.extend([pattern, pattern])
    if status:
        where_parts.append("status = %s")
        params.append(status)

    where_clause = " AND ".join(where_parts)
    # The interpolated SQL fragments come only from fixed allowlists above.
    order_clause = f"{sort_columns[sort_key]} {direction.upper()} NULLS LAST, id DESC"

    with get_cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM jobs WHERE {where_clause}",
            params,
        )
        total = cur.fetchone()[0]

        cur.execute(
            f"""
            SELECT id, title, company_name, location, work_type, match_score, status, created_at
            FROM jobs
            WHERE {where_clause}
            ORDER BY {order_clause}
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        rows = cur.fetchall()

    jobs = []
    for row in rows:
        job_id, title, company_name, location, work_type, match_score, status, created_at = row
        jobs.append({
            "id": str(job_id),
            "title": title,
            "company_name": company_name,
            "location": location,
            "work_type": work_type,
            "match_score": float(match_score) if match_score is not None else None,
            "status": status,
            "created_at": created_at.isoformat(),
        })

    total_pages = (total + per_page - 1) // per_page
    return jsonify({
        "jobs": jobs,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }), 200


#get job details
@jobs_bp.route("/<job_id>", methods = ["GET"])
@require_auth
def get_job(job_id):

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, raw_description, title, summary, no_bs_translation, skills,
                   company_name, location, work_type, match_score, match_detail,
                   status, notes, deadline, created_at
            FROM jobs
            WHERE id = %s AND user_id = %s
            """,
            (job_id, g.user_id),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job not found"}), 404

    (job_id, raw_description, title, summary, no_bs_translation, skills,
     company_name, location, work_type, match_score, match_detail,
     status, notes, deadline, created_at) = row

    return jsonify({
        "id": str(job_id),
        "raw_description": raw_description,
        "title": title,
        "summary": summary,
        "no_bs_translation": no_bs_translation,
        "skills": skills,
        "company_name": company_name,
        "location": location,
        "work_type": work_type,
        "match_score": float(match_score) if match_score is not None else None,
        "match_detail": match_detail,
        "status": status,
        "notes": notes,
        "deadline": deadline.isoformat() if deadline else None,
        "created_at": created_at.isoformat(),
    }), 200


#update job details
@jobs_bp.route("/<job_id>", methods=["PATCH"])
@require_auth
def update_job(job_id):
    data, error = get_json_object()
    if error:
        return error

    # whitelist 'allowed' a user is permitted to change
    allowed = ["status", "notes", "deadline"]
    updates = {field: data[field] for field in allowed if field in data}

    if not updates:
        return jsonify({"error": "no valid fields to update"}), 400

    if "status" in updates:
        status = updates["status"]
        if not isinstance(status, str) or status not in VALID_STATUSES:
            return jsonify({"error": "invalid status"}), 400

    if "notes" in updates:
        notes = updates["notes"]
        if notes is not None and not isinstance(notes, str):
            return jsonify({"error": "notes must be text or null"}), 400
        if isinstance(notes, str) and len(notes) > MAX_NOTES_CHARS:
            return jsonify({"error": "notes must be 5000 characters or fewer"}), 400

    if "deadline" in updates:
        deadline = updates["deadline"]
        if deadline is not None:
            if not isinstance(deadline, str):
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400
            try:
                parsed_deadline = date.fromisoformat(deadline)
            except ValueError:
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400
            if parsed_deadline.isoformat() != deadline:
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400

    set_clause = ", ".join(f"{field} = %s" for field in updates)
    values = list(updates.values())

    with get_cursor(commit=True) as cur:
        cur.execute(
            f"UPDATE jobs SET {set_clause}, updated_at = now() WHERE id = %s AND user_id = %s RETURNING id",
            values + [job_id, g.user_id],
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job not found"}), 404

    return jsonify({"id": str(row[0]), "updated": list(updates.keys())}), 200


@jobs_bp.route("/<job_id>", methods=["DELETE"])
@require_auth
def delete_job(job_id):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM jobs WHERE id = %s AND user_id = %s RETURNING id",
            (job_id, g.user_id),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job not found"}), 404

    return jsonify({"deleted": str(row[0])}), 200


@jobs_bp.route("/stats", methods = ["GET"])
@require_auth
def job_stats():
    with get_cursor() as cur:
        cur.execute(
            "SELECT status, COUNT(*) FROM jobs WHERE user_id = %s GROUP BY status",
            (g.user_id,),
        )
        rows = cur.fetchall()

    counts = {"saved": 0, "applied": 0, "interview": 0, "offer": 0,
              "rejected": 0, "ghosted": 0, "accepted": 0, "decline": 0}
    total = 0
    for status, count in rows:
        if status in counts:      # ignore unknown status
            counts[status] = count
        total += count

    return jsonify({"total": total, "by_status": counts}), 200
