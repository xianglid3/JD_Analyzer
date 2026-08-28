from flask import Blueprint, request, jsonify, g #whats g
import hashlib
import json
import logging
from os.path import splitext
from uuid import uuid4
from werkzeug.utils import secure_filename
from db import get_cursor
from middleware import require_auth
from services.match import compute_match
from services.openai_services import analyze_resume
from services.jd_preprocess import preprocess_text
from services.file_extract import extract_text_from_file, validate_resume_file
from services.supabase_storage import (
    create_resume_download_url,
    delete_resume_file,
    upload_resume_file,
)
from extensions import authenticated_user_key, limiter
from routes.analysis_errors import analysis_error_response
from routes.request_validation import get_json_object


resume_bp = Blueprint("resume", __name__, url_prefix="/api/resume") #why __name__
logger = logging.getLogger(__name__)

MIN_RESUME_CHARS = 100                                 # shared floor for /parse and /upload (BUG-023)
RESUME_LIST_FIELDS = ("education", "work_experience", "projects", "skills", "certificates")
MAX_RESUME_SKILLS = 100
MAX_SKILL_CHARS = 100
MAX_RESUME_TEXT_CHARS = 20000


def get_resume_request_data():
    """Read either the existing JSON save or a multipart save with a raw file."""
    if request.mimetype == "multipart/form-data":
        raw_payload = request.form.get("resume")
        if raw_payload is None:
            return None, (jsonify({"error": "resume form field required"}), 400), None
        try:
            data = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            return None, (jsonify({"error": "resume must be valid JSON"}), 400), None
        if not isinstance(data, dict):
            return None, (jsonify({"error": "resume must be a JSON object"}), 400), None
        return data, None, request.files.get("file")

    data, error = get_json_object()
    return data, error, None


def delete_storage_file_quietly(path):
    if not path:
        return
    try:
        delete_resume_file(path)
    except Exception:
        logger.exception("failed to clean up resume file", extra={"storage_path": path})


@resume_bp.route("", methods = ["GET"])
@require_auth
def get_resume():
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT education, work_experience, projects, skills, certificates,
                   resume_text, original_filename, file_mime_type,
                   file_size_bytes, file_uploaded_at
            FROM resumes
            WHERE user_id = %s
            """,
            (g.user_id,),
        )
        row = cur.fetchone() #what does row return?

    if row is None:
        return jsonify({"error": "no resume found"}), 404

    (education, work_experience, projects, skills, certificates,
     resume_text, original_filename, file_mime_type,
     file_size_bytes, file_uploaded_at) = row

    source_file = None
    if original_filename:
        source_file = {
            "filename": original_filename,
            "mime_type": file_mime_type,
            "size_bytes": file_size_bytes,
            "uploaded_at": file_uploaded_at.isoformat() if file_uploaded_at else None,
        }

    return jsonify({
        "education": education,
        "work_experience": work_experience,
        "projects": projects,
        "skills": skills,
        "certificates": certificates,
        "resume_text": resume_text,
        "source_file": source_file,
    }), 200


#update / insert resume
@resume_bp.route("", methods = ["PUT"])
@require_auth
def upsert_resume():
    data, error, source_file = get_resume_request_data()
    if error:
        return error

    for field in RESUME_LIST_FIELDS:
        if not isinstance(data.get(field, []), list):
            return jsonify({"error": f"{field} must be an array"}), 400

    education = data.get("education", []) #why []
    work_experience = data.get("work_experience", [])
    projects = data.get("projects", [])
    skills = data.get("skills", [])
    certificates = data.get("certificates", [])
    resume_text_provided = "resume_text" in data
    resume_text = data.get("resume_text")

    if resume_text is not None and not isinstance(resume_text, str):
        return jsonify({"error": "resume_text must be text or null"}), 400
    if isinstance(resume_text, str):
        resume_text = resume_text.strip() or None
        if resume_text and len(resume_text) > MAX_RESUME_TEXT_CHARS:
            return jsonify({"error": "resume_text must be 20000 characters or fewer"}), 400

    if len(skills) > MAX_RESUME_SKILLS:
        return jsonify({"error": "skills must contain 100 items or fewer"}), 400

    cleaned_skills = []
    seen_skills = set()
    for skill in skills:
        if not isinstance(skill, str) or not skill.strip():
            return jsonify({"error": "each skill must be non-empty text"}), 400
        skill = skill.strip()
        if len(skill) > MAX_SKILL_CHARS:
            return jsonify({"error": "each skill must be 100 characters or fewer"}), 400
        dedupe_key = skill.casefold()
        if dedupe_key not in seen_skills:
            seen_skills.add(dedupe_key)
            cleaned_skills.append(skill)
    skills = cleaned_skills

    new_storage_path = None
    original_filename = None
    file_mime_type = None
    file_size_bytes = None
    file_sha256 = None

    if source_file is not None:
        if source_file.filename == "":
            return jsonify({"error": "file name required"}), 400
        file_bytes = source_file.read()
        try:
            file_mime_type = validate_resume_file(source_file.filename, file_bytes)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        suffix = splitext(source_file.filename)[1].lower()
        original_filename = secure_filename(source_file.filename) or f"resume{suffix}"
        file_size_bytes = len(file_bytes)
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        new_storage_path = f"{g.user_id}/{uuid4().hex}{suffix}"

        try:
            upload_resume_file(new_storage_path, file_bytes, file_mime_type)
        except Exception:
            logger.exception("resume file upload failed")
            return jsonify({"error": "could not store resume file"}), 502

    old_storage_path = None
    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "SELECT storage_path FROM resumes WHERE user_id = %s FOR UPDATE",
                (g.user_id,),
            )
            existing_resume = cur.fetchone()
            old_storage_path = existing_resume[0] if existing_resume else None

            cur.execute(
                """
                INSERT INTO resumes (
                    user_id, education, work_experience, projects, skills, certificates,
                    resume_text, storage_path, original_filename, file_mime_type,
                    file_size_bytes, file_sha256, file_uploaded_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s THEN now() ELSE NULL END
                )
                ON CONFLICT (user_id) DO UPDATE SET
                    education = EXCLUDED.education,
                    work_experience = EXCLUDED.work_experience,
                    projects = EXCLUDED.projects,
                    skills = EXCLUDED.skills,
                    certificates = EXCLUDED.certificates,
                    resume_text = CASE
                        WHEN %s THEN EXCLUDED.resume_text
                        ELSE resumes.resume_text
                    END,
                    storage_path = COALESCE(EXCLUDED.storage_path, resumes.storage_path),
                    original_filename = COALESCE(EXCLUDED.original_filename, resumes.original_filename),
                    file_mime_type = COALESCE(EXCLUDED.file_mime_type, resumes.file_mime_type),
                    file_size_bytes = COALESCE(EXCLUDED.file_size_bytes, resumes.file_size_bytes),
                    file_sha256 = COALESCE(EXCLUDED.file_sha256, resumes.file_sha256),
                    file_uploaded_at = CASE
                        WHEN EXCLUDED.storage_path IS NOT NULL THEN now()
                        ELSE resumes.file_uploaded_at
                    END,
                    updated_at = now()
                """,
                (
                    g.user_id,
                    json.dumps(education),
                    json.dumps(work_experience),
                    json.dumps(projects),
                    json.dumps(skills),
                    json.dumps(certificates),
                    resume_text,
                    new_storage_path,
                    original_filename,
                    file_mime_type,
                    file_size_bytes,
                    file_sha256,
                    source_file is not None,
                    resume_text_provided,
                ),
            )

            # recompute match scores for this user's jobs against the new resume skills
            cur.execute("SELECT id, skills FROM jobs WHERE user_id = %s", (g.user_id,))
            match_updates = []
            for job_id, job_skills in cur.fetchall():
                match = compute_match(job_skills, skills)
                score = match["score"] if match else None
                detail = {
                    "matched": match["matched"],
                    "missing": match["missing"],
                } if match else None
                match_updates.append({
                    "id": str(job_id),
                    "match_score": score,
                    "match_detail": detail,
                })

            if match_updates:
                cur.execute(
                    """
                    UPDATE jobs AS job
                    SET match_score = match.match_score,
                        match_detail = match.match_detail
                    FROM jsonb_to_recordset(%s::jsonb) AS match(
                        id uuid,
                        match_score numeric,
                        match_detail jsonb
                    )
                    WHERE job.id = match.id AND job.user_id = %s
                    """,
                    (json.dumps(match_updates), g.user_id),
                )
    except Exception:
        delete_storage_file_quietly(new_storage_path)
        raise

    if new_storage_path and old_storage_path and old_storage_path != new_storage_path:
        delete_storage_file_quietly(old_storage_path)

    return jsonify({"ok": True, "file_saved": new_storage_path is not None}), 200


@resume_bp.route("/parse", methods = ["POST"])
@require_auth
@limiter.limit("5 per minute; 10 per day", key_func=authenticated_user_key)
def parse_resume():
    data, error = get_json_object()
    if error:
        return error

    resume_text = data.get("text")
    if not isinstance(resume_text, str):
        return jsonify({"error": "Resume text must be text"}), 400
    text = resume_text.strip() #get dat resume text

    #length check
    if len(text) < MIN_RESUME_CHARS:
        return jsonify({"error": "Resume text too short"}), 400
    if len(text) > MAX_RESUME_TEXT_CHARS:
        return jsonify({"error": "Resume text too long"}), 400

    #clean text
    cleaned = preprocess_text(text)

    try:
        resume = analyze_resume(cleaned)
    except Exception as exc:
        return analysis_error_response(exc, logger)
    
    return jsonify({"skills": resume.skills, "resume_text": text}), 200


@resume_bp.route("/upload", methods=["POST"])
@require_auth
@limiter.limit("5 per minute; 10 per day", key_func=authenticated_user_key)
def upload_resume():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify({"error": "no file uploaded"}), 400

    file_bytes = file.read()
    try:
        validate_resume_file(file.filename, file_bytes)
        text = extract_text_from_file(file.filename, file_bytes).strip()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("file extraction failed")
        return jsonify({"error": "could not read that file"}), 400

    text = text[:MAX_RESUME_TEXT_CHARS]   # cap very long files instead of rejecting

    if len(text) < MIN_RESUME_CHARS:
        return jsonify({"error": "couldn't extract text — is it a scanned image?"}), 400

    cleaned = preprocess_text(text)

    try:
        resume = analyze_resume(cleaned)
    except Exception as exc:
        return analysis_error_response(exc, logger)

    return jsonify({"skills": resume.skills, "resume_text": text}), 200


@resume_bp.route("/file", methods=["GET"])
@require_auth
def get_resume_file():
    with get_cursor() as cur:
        cur.execute(
            "SELECT storage_path, original_filename FROM resumes WHERE user_id = %s",
            (g.user_id,),
        )
        row = cur.fetchone()

    if row is None or row[0] is None:
        return jsonify({"error": "no resume file found"}), 404

    storage_path, original_filename = row
    try:
        signed_url = create_resume_download_url(storage_path, expires_in=60)
    except Exception:
        logger.exception("resume signed URL creation failed")
        return jsonify({"error": "could not create resume download"}), 502

    response = jsonify({
        "url": signed_url,
        "filename": original_filename,
        "expires_in": 60,
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 200

    
    
