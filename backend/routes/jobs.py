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
from services.skill_evidence import detail_for, match_for_job
from routes.analysis_errors import analysis_error_response
from routes.request_validation import get_json_object, normalize_optional_http_url
from services.usage import QuotaExceeded, check_quota, recorder


jobs_bp = Blueprint("jobs", __name__, url_prefix = "/api/jobs")
logger = logging.getLogger(__name__)
#accetpable status
VALID_STATUSES = {"saved", "applied", "interview", "offer", "rejected", "ghosted", "accepted", "decline"}
VALID_WORK_TYPES = {"remote", "hybrid", "in_person"}
IDEMPOTENCY_STALE_AFTER = "10 minutes"
IDEMPOTENCY_COMPLETED_TTL = "7 days"
MAX_NOTES_CHARS = 5000
MAX_SKILL_CHARS = 100
MAX_REVIEW_TEXT_CHARS = 300
MAX_REQUIREMENTS = 30
VALID_IMPORTANCE = {"required", "preferred", "nice_to_have"}


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
                  AND draft_id IS NULL
                """,
                (user_id, idempotency_key, request_hash),
            )
    except Exception:
        logger.exception("failed to release idempotency key")


def create_draft_payload(row, replayed=False):
    (draft_id, raw_description, title, summary, no_bs_translation, skills,
     company_name, location, work_type, source_url, expires_at, confirmed_job_id,
     requirements) = row
    payload = {
        "id": str(draft_id),
        "raw_description": raw_description,
        "title": title,
        "summary": summary,
        "no_bs_translation": no_bs_translation,
        "skills": skills,
        "requirements": requirements,
        "company_name": company_name,
        "location": location,
        "work_type": work_type,
        "source_url": source_url,
        "expires_at": expires_at.isoformat(),
        "confirmed_job_id": str(confirmed_job_id) if confirmed_job_id else None,
    }
    if replayed:
        payload["replayed"] = True
    return payload


def normalize_requirements(value):
    """Validate the user's corrected requirement list.

    Extraction gets importance wrong often enough that the review screen has to be able to
    fix it — a bad requirement otherwise scores every future match against this job.
    """
    if not isinstance(value, list):
        return None, "requirements must be an array"
    if len(value) > MAX_REQUIREMENTS:
        return None, f"requirements must contain {MAX_REQUIREMENTS} items or fewer"

    cleaned = []
    seen = set()
    for item in value:
        if isinstance(item, str):
            skill, importance = item, "required"
        elif isinstance(item, dict):
            skill, importance = item.get("skill"), item.get("importance", "required")
        else:
            return None, "each requirement must be an object"

        if not isinstance(skill, str) or not skill.strip():
            return None, "each requirement needs a skill"
        skill = skill.strip()
        if len(skill) > MAX_SKILL_CHARS:
            return None, f"each skill must be {MAX_SKILL_CHARS} characters or fewer"
        if importance not in VALID_IMPORTANCE:
            return None, "importance must be required, preferred, or nice_to_have"

        key = skill.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append({"skill": skill, "importance": importance})

    return cleaned, None


def normalize_review_text(value, field, required=False):
    if value is None and not required:
        return None, None
    if not isinstance(value, str):
        return None, f"{field} must be text"
    value = value.strip()
    if required and not value:
        return None, f"{field} is required"
    if len(value) > MAX_REVIEW_TEXT_CHARS:
        return None, f"{field} must be {MAX_REVIEW_TEXT_CHARS} characters or fewer"
    return value or None, None


@jobs_bp.route("/drafts", methods=["POST"])
@require_auth
@limiter.limit("5 per minute; 50 per day", key_func=authenticated_user_key)
def create_job_draft():
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

    # Validate the optional original job-posting URL.
    source_url, url_error = normalize_optional_http_url(data.get("source_url"))
    if url_error:
        return jsonify({"error": url_error}), 400

    #job idempotency stuff
    idempotency_key = request.headers.get("Idempotency-Key", "")
    try:
        UUID(idempotency_key)
    except ValueError:
        return jsonify({"error": "Valid Idempotency-Key required"}), 400

    request_hash = hashlib.sha256(
        json.dumps(
            {"description": raw_description, "source_url": source_url},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    # Drafts expire after 30 minutes. Confirmed responses replay for 7 days;
    # deleting their reservation after that intentionally ends the replay guarantee.
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            DELETE FROM job_analysis_drafts
            WHERE user_id = %s
              AND (
                  (confirmed_job_id IS NULL AND expires_at <= now())
                  OR
                  (confirmed_job_id IS NOT NULL AND created_at < now() - %s::interval)
              )
            """,
            (g.user_id, IDEMPOTENCY_COMPLETED_TTL),
        )
        cur.execute(
            """
            DELETE FROM idempotency_requests
            WHERE user_id = %s
              AND (
                  (job_id IS NULL AND draft_id IS NULL AND created_at < now() - %s::interval)
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
                SELECT r.request_hash, r.job_id, r.draft_id,
                       d.id, d.raw_description, d.title, d.summary,
                       d.no_bs_translation, d.skills, d.company_name,
                       d.location, d.work_type, d.source_url, d.expires_at,
                       d.confirmed_job_id, d.requirements
                FROM idempotency_requests AS r
                LEFT JOIN job_analysis_drafts AS d ON d.id = r.draft_id
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
                "error": "Idempotency-Key was already used with a different request"
            }), 409
        if existing[1] is not None:
            return jsonify({
                "id": str(existing[1]),
                "confirmed": True,
                "replayed": True,
            }), 200
        if existing[2] is None or existing[3] is None:
            response = jsonify({"error": "Analysis already in progress, please retry shortly"})
            response.headers["Retry-After"] = "3"
            return response, 409
        return jsonify(create_draft_payload(existing[3:], replayed=True)), 200

    try:
        check_quota(g.user_id)
    except QuotaExceeded as exc:
        release_idempotency_key(g.user_id, idempotency_key, request_hash)
        return jsonify({"error": f"daily AI budget reached ({exc})"}), 429

    cleaned_description = preprocess_text(raw_description)

    # AI call stays OUTSIDE the db connection — don't hold a connection open for 3-8s
    try:
        job = analyze_job_description(cleaned_description, on_usage=recorder(g.user_id, "job_analysis"))
    except Exception as exc:
        release_idempotency_key(g.user_id, idempotency_key, request_hash)
        return analysis_error_response(exc, logger)

    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO job_analysis_drafts (
                    user_id, raw_description, title, summary, no_bs_translation,
                    skills, requirements, company_name, location, work_type, source_url
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, raw_description, title, summary, no_bs_translation, skills,
                          company_name, location, work_type, source_url, expires_at,
                          confirmed_job_id, requirements
                """,
                (
                    g.user_id, raw_description, job.title, job.summary, job.no_bs_translation,
                    json.dumps(job.skills),
                    json.dumps([r.model_dump() for r in job.requirements]),
                    job.company_name, job.location, job.work_type,
                    source_url,
                ),
            )
            draft_row = cur.fetchone()

            cur.execute(
                """
                UPDATE idempotency_requests
                SET draft_id = %s
                WHERE user_id = %s
                  AND idempotency_key = %s
                  AND request_hash = %s
                  AND job_id IS NULL
                  AND draft_id IS NULL
                """,
                (draft_row[0], g.user_id, idempotency_key, request_hash),
            )
            if cur.rowcount != 1:
                raise RuntimeError("idempotency reservation disappeared")
    except Exception:
        release_idempotency_key(g.user_id, idempotency_key, request_hash)
        raise

    return jsonify(create_draft_payload(draft_row)), 201


@jobs_bp.route("/drafts/<draft_id>", methods=["GET"])
@require_auth
def get_job_draft(draft_id):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, raw_description, title, summary, no_bs_translation, skills,
                   company_name, location, work_type, source_url, expires_at,
                   confirmed_job_id, requirements, expires_at <= now()
            FROM job_analysis_drafts
            WHERE id = %s AND user_id = %s
            """,
            (draft_id, g.user_id),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job draft not found"}), 404

    *payload, confirmed_job_id, requirements, expired = row
    if expired and confirmed_job_id is None:
        return jsonify({"error": "job draft expired"}), 410
    return jsonify(create_draft_payload((*payload, confirmed_job_id, requirements))), 200


@jobs_bp.route("/drafts/<draft_id>", methods=["DELETE"])
@require_auth
def delete_job_draft(draft_id):
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            SELECT confirmed_job_id
            FROM job_analysis_drafts
            WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (draft_id, g.user_id),
        )
        row = cur.fetchone()
        if row is None:
            return jsonify({"error": "job draft not found"}), 404
        if row[0] is not None:
            return jsonify({"error": "confirmed job drafts cannot be cancelled"}), 409

        cur.execute(
            "DELETE FROM idempotency_requests WHERE user_id = %s AND draft_id = %s AND job_id IS NULL",
            (g.user_id, draft_id),
        )
        cur.execute(
            "DELETE FROM job_analysis_drafts WHERE id = %s AND user_id = %s",
            (draft_id, g.user_id),
        )

    return jsonify({"deleted": draft_id}), 200


@jobs_bp.route("/drafts/<draft_id>/confirm", methods=["POST"])
@require_auth
def confirm_job_draft(draft_id):
    data, error = get_json_object()
    if error:
        return error

    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            SELECT raw_description, title, summary, no_bs_translation, skills,
                   company_name, location, work_type, source_url,
                   confirmed_job_id, expires_at <= now(), requirements
            FROM job_analysis_drafts
            WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (draft_id, g.user_id),
        )
        draft = cur.fetchone()

        if draft is None:
            return jsonify({"error": "job draft not found"}), 404
        if draft[9] is not None:
            return jsonify({"id": str(draft[9]), "replayed": True}), 200
        if draft[10]:
            return jsonify({"error": "job draft expired"}), 410

        (raw_description, default_title, summary, no_bs_translation, skills,
         default_company, default_location, default_work_type, default_source_url,
         _confirmed_job_id, _expired, requirements) = draft

        title, validation_error = normalize_review_text(data.get("title", default_title), "title", required=True)
        if validation_error:
            return jsonify({"error": validation_error}), 400
        company_name, validation_error = normalize_review_text(
            data.get("company_name", default_company), "company_name"
        )
        if validation_error:
            return jsonify({"error": validation_error}), 400
        location, validation_error = normalize_review_text(data.get("location", default_location), "location")
        if validation_error:
            return jsonify({"error": validation_error}), 400

        work_type = data.get("work_type", default_work_type)
        if work_type is not None and work_type not in VALID_WORK_TYPES:
            return jsonify({"error": "invalid work_type"}), 400

        source_url, url_error = normalize_optional_http_url(data.get("source_url", default_source_url))
        if url_error:
            return jsonify({"error": url_error}), 400

        if "requirements" in data:
            requirements, requirement_error = normalize_requirements(data["requirements"])
            if requirement_error:
                return jsonify({"error": requirement_error}), 400
            # skills is the flat view of the same list; regenerating it here stops the two
            # from disagreeing after an edit
            skills = [item["skill"] for item in requirements]

        status = data.get("status", "saved")
        if not isinstance(status, str) or status not in VALID_STATUSES:
            return jsonify({"error": "invalid status"}), 400

        deadline = data.get("deadline")
        if deadline is not None:
            if not isinstance(deadline, str):
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400
            try:
                parsed_deadline = date.fromisoformat(deadline)
            except ValueError:
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400
            if parsed_deadline.isoformat() != deadline:
                return jsonify({"error": "deadline must be YYYY-MM-DD or null"}), 400

        match = match_for_job(cur, g.user_id, requirements, skills)
        match_score = match["score"] if match else None
        match_detail = detail_for(match)

        cur.execute(
            """
            INSERT INTO jobs (
                user_id, raw_description, title, summary, no_bs_translation,
                skills, requirements, company_name, location, work_type, source_url,
                match_score, match_detail, status, deadline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                g.user_id, raw_description, title, summary, no_bs_translation,
                json.dumps(skills), json.dumps(requirements), company_name, location, work_type, source_url,
                match_score, json.dumps(match_detail) if match_detail else None,
                status, deadline,
            ),
        )
        job_id = cur.fetchone()[0]

        cur.execute(
            """
            UPDATE job_analysis_drafts
            SET confirmed_job_id = %s, updated_at = now()
            WHERE id = %s AND user_id = %s
            """,
            (job_id, draft_id, g.user_id),
        )
        cur.execute(
            """
            UPDATE idempotency_requests
            SET job_id = %s
            WHERE user_id = %s AND draft_id = %s AND job_id IS NULL
            """,
            (job_id, g.user_id, draft_id),
        )

    return jsonify({"id": str(job_id)}), 201


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
    statuses = [value.strip() for value in request.args.getlist("status") if value.strip()]
    sort_key = request.args.get("sort", "created_at")
    direction = request.args.get("direction", "desc").lower()

    if len(statuses) > len(VALID_STATUSES) or any(status not in VALID_STATUSES for status in statuses):
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
    if statuses:
        placeholders = ", ".join(["%s"] * len(statuses))
        where_parts.append(f"status IN ({placeholders})")
        params.extend(statuses)

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
                   requirements, company_name, location, work_type, match_score, match_detail,
                   status, notes, deadline, source_url, created_at
            FROM jobs
            WHERE id = %s AND user_id = %s
            """,
            (job_id, g.user_id),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job not found"}), 404

    (job_id, raw_description, title, summary, no_bs_translation, skills,
     requirements, company_name, location, work_type, match_score, match_detail,
     status, notes, deadline, source_url, created_at) = row

    return jsonify({
        "id": str(job_id),
        "raw_description": raw_description,
        "title": title,
        "summary": summary,
        "no_bs_translation": no_bs_translation,
        "skills": skills,
        "requirements": requirements,
        "company_name": company_name,
        "location": location,
        "work_type": work_type,
        "match_score": float(match_score) if match_score is not None else None,
        "match_detail": match_detail,
        "status": status,
        "notes": notes,
        "deadline": deadline.isoformat() if deadline else None,
        "source_url": source_url,
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
    allowed = ["status", "notes", "deadline", "source_url"]
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

    if "source_url" in updates:
        source_url, url_error = normalize_optional_http_url(updates["source_url"])
        if url_error:
            return jsonify({"error": url_error}), 400
        updates["source_url"] = source_url

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
