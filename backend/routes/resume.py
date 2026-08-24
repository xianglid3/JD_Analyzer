from flask import Blueprint, request, jsonify, g #whats g
import json
import logging
from os.path import splitext
from db import get_cursor
from middleware import require_auth
from services.match import compute_match_score
from services.openai_services import analyze_resume
from services.jd_preprocess import preprocess_text
from services.file_extract import extract_text_from_file
from extensions import limiter


resume_bp = Blueprint("resume", __name__, url_prefix="/api/resume") #why __name__
logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".html"}   # server-side allowlist (not the UI hint)
MIN_RESUME_CHARS = 100                                 # shared floor for /parse and /upload (BUG-023)


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


@resume_bp.route("/parse", methods = ["POST"])
@limiter.limit("5 per minute; 10 per day")
@require_auth
def parse_resume():
    data = request.get_json() or {}
    text = (data.get("text") or "").strip() #get dat resume text

    #length check
    if len(text) < MIN_RESUME_CHARS:
        return jsonify({"error": "Resume text too short"}), 400
    if (len(text) > 20000):
        return jsonify({"error": "Resume text too long"}), 400

    #clean text
    cleaned = preprocess_text(text)

    try:
        resume = analyze_resume(cleaned)
    except Exception:
        logger.exception("analyze_resume failed (parse)")
        return jsonify({"error": "Analysis failed, please try again"}), 503
    
    return jsonify({"skills": resume.skills}), 200


@resume_bp.route("/upload", methods=["POST"])
@limiter.limit("5 per minute; 10 per day")
@require_auth
def upload_resume():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify({"error": "no file uploaded"}), 400

    if splitext(file.filename)[1].lower() not in ALLOWED_SUFFIXES:
        return jsonify({"error": "unsupported file type (pdf, txt, md, html only)"}), 400

    try:
        text = extract_text_from_file(file.filename, file.read()).strip()
    except Exception:
        logger.exception("file extraction failed")
        return jsonify({"error": "could not read that file"}), 400

    text = text[:20000]   # cap very long files instead of rejecting

    if len(text) < MIN_RESUME_CHARS:
        return jsonify({"error": "couldn't extract text — is it a scanned image?"}), 400

    cleaned = preprocess_text(text)

    try:
        resume = analyze_resume(cleaned)
    except Exception:
        logger.exception("analyze_resume failed (upload)")
        return jsonify({"error": "analysis failed, please try again"}), 503

    return jsonify({"skills": resume.skills}), 200

    
    
