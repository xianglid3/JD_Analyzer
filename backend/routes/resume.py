from flask import Blueprint, request, jsonify, g #whats g
import json
from db import get_cursor
from middleware import require_auth
from services.match import compute_match_score

resume_bp = Blueprint("resume", __name__, url_prefix="/api/resume") #why __name__


@resume_bp.route("", methods = ["GET"])
@require_auth
def get_resume():
    with get_cursor() as cur:
        cur.execute(
            "SELECT education, work_experience, projects, skills, certificates FROM resumes WHERE user_id = %s",
            (g.user_id,),
        )
        row = cur.fetchone() #what does row return?

    if row is None:
        return jsonify({"error": "no resume found"}), 404

    education, work_experience, projects, skills, certificates = row

    return jsonify({
        "education": education,
        "work_experience": work_experience,
        "projects": projects,
        "skills": skills,
        "certificates": certificates,
    }), 200


#update / insert resume
@resume_bp.route("", methods = ["PUT"])
@require_auth
def upsert_resume():
    data = request.get_json() or {} #?
    education = data.get("education", []) #why []
    work_experience = data.get("work_experience", [])
    projects = data.get("projects", [])
    skills = data.get("skills", [])
    certificates = data.get("certificates", [])

    with get_cursor(commit=True) as cur:
        # insert into the field, with values, and when user_id conflit
        # update
        # EXCLUDED -> the conflict-insert data
        cur.execute(
            """
            INSERT INTO resumes (user_id, education, work_experience, projects, skills, certificates)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                education = EXCLUDED.education,
                work_experience = EXCLUDED.work_experience,
                projects = EXCLUDED.projects,
                skills = EXCLUDED.skills,
                certificates = EXCLUDED.certificates,
                updated_at = now()
            """,
            (
                g.user_id, #whats g from flask
                json.dumps(education), # dumps: convert python obj -> JSON string
                json.dumps(work_experience),
                json.dumps(projects),
                json.dumps(skills),
                json.dumps(certificates),
            ),
        )

        # recompute match scores for this user's jobs against the new resume skills
        cur.execute("SELECT id, skills FROM jobs WHERE user_id = %s", (g.user_id,))
        for job_id, job_skills in cur.fetchall():
            score = compute_match_score(job_skills, skills)
            cur.execute("UPDATE jobs SET match_score = %s WHERE id = %s", (score, job_id))

    return jsonify({"ok": True}), 200
