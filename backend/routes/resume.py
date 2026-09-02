from flask import Blueprint, request, jsonify, g #whats g
import hashlib
import json
import logging
from os.path import splitext
from uuid import uuid4
from werkzeug.utils import secure_filename
from db import get_cursor
from middleware import require_auth
from pydantic import ValidationError
from services.openai_services import (
    ResumeHeader,
    ResumeStructure,
    analyze_resume,
    analyze_resume_structure,
    enforce_structure_limits,
)
from services.jd_preprocess import preprocess_text
from services.file_extract import extract_text_from_file, validate_resume_file
from services.skill_evidence import recompute_user_matches
from services.resume_evidence import (
    evidence_is_stale,
    list_resume_evidence,
    load_resume_header,
    save_resume_evidence,
    save_resume_header,
    set_evidence_source,
    source_hash,
)
from services.supabase_storage import (
    create_resume_download_url,
    delete_resume_file,
    upload_resume_file,
)
from extensions import authenticated_user_key, limiter
from routes.analysis_errors import analysis_error_response
from routes.request_validation import get_json_object
from services.usage import QuotaExceeded, check_quota, recorder


resume_bp = Blueprint("resume", __name__, url_prefix="/api/resume") #why __name__
logger = logging.getLogger(__name__)

MIN_RESUME_CHARS = 100                                 # shared floor for /parse and /upload (BUG-023)
RESUME_LIST_FIELDS = ("education", "work_experience", "projects", "skills", "certificates")
MAX_RESUME_SKILLS = 100
MAX_SKILL_CHARS = 100
MAX_RESUME_TEXT_CHARS = 20000
MAX_ENTRY_FIELD_CHARS = 200
MAX_HEADER_FIELD_CHARS = 200
MAX_BULLET_CHARS = 500


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

            # rescore against the structured evidence, not just the flat skills list
            recompute_user_matches(cur, g.user_id)
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
        check_quota(g.user_id)
        resume = analyze_resume(cleaned, on_usage=recorder(g.user_id, "resume_parse"))
    except QuotaExceeded as exc:
        return jsonify({"error": f"daily AI budget reached ({exc})"}), 429
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
        check_quota(g.user_id)
        resume = analyze_resume(cleaned, on_usage=recorder(g.user_id, "resume_upload"))
    except QuotaExceeded as exc:
        return jsonify({"error": f"daily AI budget reached ({exc})"}), 429
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


@resume_bp.route("/file", methods=["DELETE"])
@require_auth
def delete_saved_resume_file():
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT storage_path FROM resumes WHERE user_id = %s FOR UPDATE",
            (g.user_id,),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return jsonify({"error": "no resume file found"}), 404

        storage_path = row[0]
        cur.execute(
            """
            UPDATE resumes
            SET storage_path = NULL,
                original_filename = NULL,
                file_mime_type = NULL,
                file_size_bytes = NULL,
                file_sha256 = NULL,
                file_uploaded_at = NULL,
                updated_at = now()
            WHERE user_id = %s
            """,
            (g.user_id,),
        )

    # Postgres is the source of truth. If Storage is temporarily unavailable,
    # reconciliation will remove the now-unreferenced private object later.
    delete_storage_file_quietly(storage_path)
    return jsonify({"ok": True}), 200


def validate_structure(data):
    """Validate the reviewed structure — the same rules the model's output goes through,
    applied to the version the user corrected."""
    header_data = data.get("header") or {}
    if not isinstance(header_data, dict):
        return None, "header must be an object"
    for field in ("full_name", "email", "phone", "location"):
        value = header_data.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None, f"{field} must be text or null"
        if len(value) > MAX_HEADER_FIELD_CHARS:
            return None, f"{field} must be {MAX_HEADER_FIELD_CHARS} characters or fewer"
    links = header_data.get("links", [])
    if not isinstance(links, list) or any(not isinstance(link, str) for link in links):
        return None, "links must be an array of text"
    if any(len(link) > MAX_HEADER_FIELD_CHARS for link in links):
        return None, f"each link must be {MAX_HEADER_FIELD_CHARS} characters or fewer"

    entries = data.get("entries")
    if not isinstance(entries, list):
        return None, "entries must be an array"

    claimed_ids = set()

    for entry in entries:
        if not isinstance(entry, dict):
            return None, "each entry must be an object"
        for field in ("organization", "title", "location", "start_date", "end_date"):
            value = entry.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                return None, f"{field} must be text or null"
            if len(value) > MAX_ENTRY_FIELD_CHARS:
                return None, f"{field} must be {MAX_ENTRY_FIELD_CHARS} characters or fewer"
        bullets = entry.get("bullets", [])
        if not isinstance(bullets, list):
            return None, "bullets must be an array"
        for bullet in bullets:
            # a bullet is either new text or an existing one being edited, which carries its id
            if isinstance(bullet, dict):
                text, bullet_id = bullet.get("text"), bullet.get("id")
                if bullet_id is not None and not isinstance(bullet_id, str):
                    return None, "bullet id must be text"
                if bullet_id is not None:
                    if bullet_id in claimed_ids:
                        return None, "the same bullet id was sent twice"
                    claimed_ids.add(bullet_id)
            elif isinstance(bullet, str):
                text = bullet
            else:
                return None, "each bullet must be text"
            if not isinstance(text, str):
                return None, "each bullet must be text"
            if len(text) > MAX_BULLET_CHARS:
                return None, f"each bullet must be {MAX_BULLET_CHARS} characters or fewer"

    try:
        structure = ResumeStructure(
            header=ResumeHeader(
                full_name=(header_data.get("full_name") or None),
                email=(header_data.get("email") or None),
                phone=(header_data.get("phone") or None),
                location=(header_data.get("location") or None),
                links=links,
            ),
            entries=entries,
            skills=[],   # the client sends skills as a `skill` entry, not separately
        )
    except ValidationError as exc:
        # the only field that can still fail is `kind`, which is a fixed enum
        logger.info("resume structure rejected: %s", exc.error_count())
        return None, "each entry needs a kind of experience, project, education, or certificate"

    return enforce_structure_limits(structure), None


@resume_bp.route("/structure", methods=["POST"])
@require_auth
@limiter.limit("5 per minute; 10 per day", key_func=authenticated_user_key)
def extract_resume_structure():
    """Extract entries and bullets. Saves nothing — the user reviews first, same as
    /parse and /upload."""
    data, error = get_json_object()
    if error:
        return error

    text = data.get("text")
    if text is None:
        with get_cursor() as cur:
            cur.execute("SELECT resume_text FROM resumes WHERE user_id = %s", (g.user_id,))
            row = cur.fetchone()
        text = row[0] if row else None
        if not text:
            return jsonify({"error": "no saved resume text — paste or upload a resume first"}), 404

    if not isinstance(text, str):
        return jsonify({"error": "Resume text must be text"}), 400

    text = text.strip()
    if len(text) < MIN_RESUME_CHARS:
        return jsonify({"error": "Resume text too short"}), 400
    if len(text) > MAX_RESUME_TEXT_CHARS:
        return jsonify({"error": "Resume text too long"}), 400

    cleaned = preprocess_text(text)

    try:
        check_quota(g.user_id)
        structure = analyze_resume_structure(cleaned, on_usage=recorder(g.user_id, "resume_structure"))
    except QuotaExceeded as exc:
        return jsonify({"error": f"daily AI budget reached ({exc})"}), 429
    except Exception as exc:
        return analysis_error_response(exc, logger)

    # bullets go out as plain strings: a freshly extracted bullet has no id yet, and
    # sending `{id: null}` would invite the client to make one up
    return jsonify({
        "header": structure.header.model_dump(),
        "entries": [
            {**entry.model_dump(exclude={"bullets"}),
             "bullets": [bullet.text for bullet in entry.bullets]}
            for entry in structure.entries
        ],
        "skills": structure.skills,
        # the client sends this back on save, so evidence is stamped with the text it was
        # actually extracted from — stamping at save time would bless whatever is current
        "source_hash": source_hash(text),
    }), 200


@resume_bp.route("/evidence", methods=["GET"])
@require_auth
def get_resume_evidence():
    with get_cursor() as cur:
        entries = list_resume_evidence(cur, g.user_id)
        header = load_resume_header(cur, g.user_id)
        stale = evidence_is_stale(cur, g.user_id)
        cur.execute("SELECT evidence_source_hash FROM resumes WHERE user_id = %s", (g.user_id,))
        row = cur.fetchone()
    return jsonify({
        "header": header, "entries": entries, "stale": stale,
        "source_hash": row[0] if row else None,
    }), 200


@resume_bp.route("/evidence", methods=["PUT"])
@require_auth
def upsert_resume_evidence():
    """Commit the reviewed structure. Unchanged bullets keep their ids, so earlier citations
    stay valid."""
    data, error = get_json_object()
    if error:
        return error

    structure, validation_error = validate_structure(data)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    submitted_hash = data.get("source_hash")
    if submitted_hash is not None and not isinstance(submitted_hash, str):
        return jsonify({"error": "source_hash must be text"}), 400

    with get_cursor(commit=True) as cur:
        save_resume_header(cur, g.user_id, structure.header)
        stats = save_resume_evidence(cur, g.user_id, structure)

        if submitted_hash:
            # the extraction this evidence came from says which text it describes
            cur.execute(
                "UPDATE resumes SET evidence_source_hash = %s WHERE user_id = %s",
                (submitted_hash, g.user_id),
            )
        else:
            # No extraction behind this save. Stamping the current text here would let
            # someone clear a stale warning by opening the editor and pressing save, which
            # is exactly the lie the flag exists to prevent — so an existing stamp is kept
            # and only never-extracted evidence gets one.
            cur.execute(
                "SELECT resume_text, evidence_source_hash FROM resumes WHERE user_id = %s",
                (g.user_id,),
            )
            row = cur.fetchone()
            if row and row[1] is None:
                set_evidence_source(cur, g.user_id, row[0])

        recompute_user_matches(cur, g.user_id)
        entries = list_resume_evidence(cur, g.user_id)
        header = load_resume_header(cur, g.user_id)
        stale = evidence_is_stale(cur, g.user_id)

    logger.info(
        "resume evidence saved entries=%d bullets=%d reused=%d",
        stats["entries"], stats["bullets"], stats["reused_bullets"],
    )
    return jsonify({"header": header, "entries": entries, "saved": stats, "stale": stale}), 200
