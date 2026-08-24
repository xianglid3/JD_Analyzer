from flask import Blueprint, request, jsonify, g
import json
import logging
from db import get_cursor
from middleware import require_auth
from extensions import limiter
from services.jd_preprocess import preprocess_text
from services.openai_services import analyze_job_description
from services.match import compute_match_score


jobs_bp = Blueprint("jobs", __name__, url_prefix = "/api/jobs")
logger = logging.getLogger(__name__)
#accetpable status
VALID_STATUSES = {"saved", "applied", "interview", "offer", "rejected", "ghosted", "accepted", "decline"}


@jobs_bp.route("", methods = ["POST"])
@limiter.limit("5 per minute; 50 per day")
@require_auth
def create_job():
    data = request.get_json()
    raw_description = (data.get("description") or "").strip()

    #validation length
    if len(raw_description) < 50:
        return jsonify({"error": "Description too short"}), 400
    if len(raw_description) > 10000:
        return jsonify({"error": "Description too long"}), 400

    cleaned_description = preprocess_text(raw_description)

    # AI call stays OUTSIDE the db connection — don't hold a connection open for 3-8s
    try:
        job = analyze_job_description(cleaned_description)
    except Exception:
        logger.exception("analyze_job_description failed")
        return jsonify({"error": "analysis failed, please try again"}), 503

    with get_cursor(commit=True) as cur:
        cur.execute("SELECT skills FROM resumes WHERE user_id = %s", (g.user_id,))
        resume_row = cur.fetchone()
        resume_skills = resume_row[0] if resume_row else []
        match_score = compute_match_score(job.skills, resume_skills)

        cur.execute(
            # triple quote to span across lines
            """
            INSERT INTO jobs (user_id, raw_description, title, summary, no_bs_translation,
                              skills, company_name, location, work_type, match_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (
                g.user_id, raw_description, job.title, job.summary, job.no_bs_translation,
                json.dumps(job.skills), job.company_name, job.location, job.work_type, match_score
            ),
        )
        new_id, created_at = cur.fetchone()

    return jsonify({
        "id": str(new_id),
        "title": job.title,
        "summary": job.summary,
        "no_bs_translation": job.no_bs_translation,
        "skills": job.skills,
        "company_name": job.company_name,
        "location": job.location,
        "work_type": job.work_type,
        "created_at": created_at.isoformat(),
    }), 201


#list all of the saved jobs from a user
@jobs_bp.route("", methods = ["GET"])
@require_auth
def list_jobs():

    #pagination, so we don't get 5000 items all at once
    page = int(request.args.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, company_name, location, work_type, match_score, status, created_at
            FROM jobs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (g.user_id, per_page, offset),
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

    return jsonify({"jobs": jobs, "page": page}), 200


#get job details
@jobs_bp.route("/<job_id>", methods = ["GET"])
@require_auth
def get_job(job_id):

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, raw_description, title, summary, no_bs_translation, skills,
                   company_name, location, work_type, match_score, status, notes, deadline, created_at
            FROM jobs
            WHERE id = %s AND user_id = %s
            """,
            (job_id, g.user_id),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "job not found"}), 404

    (job_id, raw_description, title, summary, no_bs_translation, skills,
     company_name, location, work_type, match_score, status, notes, deadline, created_at) = row

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
        "status": status,
        "notes": notes,
        "deadline": deadline.isoformat() if deadline else None,
        "created_at": created_at.isoformat(),
    }), 200


#update job details
@jobs_bp.route("/<job_id>", methods=["PATCH"])
@require_auth
def update_job(job_id):
    data = request.get_json() or {}

    # whitelist 'allowed' a user is permitted to change
    allowed = ["status", "notes", "deadline"]
    updates = {field: data[field] for field in allowed if field in data}

    if not updates:
        return jsonify({"error": "no valid fields to update"}), 400

    if "status" in updates and updates["status"] not in VALID_STATUSES:
        return jsonify({"error": "invalid status"}), 400

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
