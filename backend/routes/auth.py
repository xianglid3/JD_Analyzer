from flask import Blueprint, request, jsonify, g
from db import get_cursor
from config import env_flag, env_int, env_str
from middleware import require_auth
from extensions import credential_key, limiter
import psycopg2
import bcrypt
import secrets
import hashlib
import jwt
import datetime
import os
import hmac
import re

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
JWT_SECRET = os.environ["JWT_SECRET"]
COOKIE_SECURE = env_flag("COOKIE_SECURE")

# Local development runs on plain HTTP, so Secure defaults off — but shipping that way sends
# the session cookie in clear text on every request. Production has to say so out loud rather
# than discover it later. (Secure and HttpOnly are different protections: HttpOnly keeps
# JavaScript out of the cookie, Secure keeps it off unencrypted connections. This is the
# second one.)
# The frontend and the API live on different subdomains in production (app.* and api.*), so
# a cookie scoped to the API host alone would never be sent with a request from the app host.
# `.yoursite.com` is what makes them one site. Empty locally, where the Vite proxy already
# makes everything same-origin.
COOKIE_DOMAIN = env_str("COOKIE_DOMAIN") or None

if env_str("APP_ENV").lower() == "production" and not COOKIE_SECURE:
    raise RuntimeError(
        "COOKIE_SECURE must be true when APP_ENV=production — auth cookies would be sent "
        "over plain HTTP"
    )

# Signup friction. Per-user spend caps bound one account; accounts have to cost something
# more than an HTTP request or the global cap is the only thing between a script and the
# OpenAI key. Both are off by default, so local development is unaffected.
#   SIGNUP_INVITE_CODE — when set, a signup must present it
#   MAX_SIGNUPS_PER_DAY — when set, the whole service accepts at most this many a day
SIGNUP_INVITE_CODE = env_str("SIGNUP_INVITE_CODE")
MAX_SIGNUPS_PER_DAY = env_int("MAX_SIGNUPS_PER_DAY", 0)

#validation limits
MIN_USERNAME_CHARS = 3
MAX_USERNAME_CHARS = 50
MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

def validate_credentials(data):
    if not isinstance(data, dict):
        return None, None, "Username and password required"

    username = data.get("username")
    password = data.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return None, None, "Username and password required"

    username = username.strip()
    password_bytes = password.encode("utf-8")

    if len(username) < MIN_USERNAME_CHARS:
        return None, None, f"Username must be at least {MIN_USERNAME_CHARS} characters"

    if len(username) > MAX_USERNAME_CHARS:
        return None, None, f"Username must be {MAX_USERNAME_CHARS} characters or fewer"

    if not USERNAME_PATTERN.fullmatch(username):
        return None, None, "Username can only contain letters and numbers"

    if any(char.isspace() for char in password):
        return None, None, "Password cannot contain spaces"

    if len(password_bytes) < MIN_PASSWORD_BYTES:
        return None, None, f"Password must be at least {MIN_PASSWORD_BYTES} characters"

    # bcrypt truncates past 72 bytes, and an accented character costs more than one
    if len(password_bytes) > MAX_PASSWORD_BYTES:
        return None, None, "Password is too long — please use something shorter"

    return username, password, None


@auth_bp.route("/me", methods = ["GET"])
@require_auth
def me():
    with get_cursor() as cur:
        cur.execute("SELECT id, username FROM users WHERE id = %s", (g.user_id,))
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "user not found"}), 404

    user_id, username = row
    return jsonify({"id": str(user_id), "username": username}), 200


@auth_bp.route("/config", methods=["GET"])
def signup_config():
    """What the signup form needs to know before it renders.

    Only whether a code is required — never the code itself, and nothing about the daily
    ceiling, which would tell a script how many accounts are left to take.
    """
    return jsonify({"invite_required": bool(SIGNUP_INVITE_CODE)}), 200


@auth_bp.route("/signup", methods=["POST"])
@limiter.limit("5 per minute; 20 per hour")
def signup():

    # silent=true prevents bad JSON causing flask error before validation run
    data = request.get_json(silent=True)
    username, password, error = validate_credentials(data)

    if error:
        return jsonify({"error": error}), 400

    if SIGNUP_INVITE_CODE:
        # compare_digest, not ==: a plain comparison returns faster on an early mismatch, and
        # that timing is enough to recover the code one character at a time
        offered = (data or {}).get("invite_code") or ""
        if not hmac.compare_digest(str(offered), SIGNUP_INVITE_CODE):
            return jsonify({"error": "this service is invite-only"}), 403

    if MAX_SIGNUPS_PER_DAY:
        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM users WHERE created_at >= current_date")
            if cur.fetchone()[0] >= MAX_SIGNUPS_PER_DAY:
                # deliberately not "come back tomorrow, we are full" — that tells a script
                # exactly what it hit and when to retry
                return jsonify({"error": "signups are temporarily closed"}), 503

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    try:
        with get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users(username, password_hash) VALUES (%s, %s) RETURNING id", (username, password_hash),
            )
            user_id = cur.fetchone()[0]

    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "username already taken"}), 409

    return jsonify({"id": user_id, "username": username}), 201


@auth_bp.route("/login", methods =["POST"])
@limiter.limit("10 per minute; 60 per hour")
@limiter.limit("5 per minute; 20 per hour", key_func=credential_key)
def login():
    data = request.get_json(silent=True)
    username, password, error = validate_credentials(data)

    if error:
        return jsonify({"error": error}), 400

    with get_cursor() as cur:
        cur.execute(
            "SELECT id, password_hash From users Where username = %s", (username,),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "invalid credentials"}), 401

    user_id, password_hash = row

    if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
        return jsonify({"error": "invalid credentials"}), 401

    #JWT access token
    payload = {
        "user_id": str(user_id),
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15),
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    response = jsonify({"id": str(user_id), "username": username})
    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="Lax",
        secure=COOKIE_SECURE,
        domain=COOKIE_DOMAIN,
        max_age=15 * 60,
    )

    # refresh token via hashed string
    refresh_raw = generate_refresh_token(user_id)

    response.set_cookie(
        "refresh_token",
        refresh_raw,
        httponly=True,
        samesite="Lax",
        secure=COOKIE_SECURE,
        domain=COOKIE_DOMAIN,
        max_age=30 * 24 * 60 * 60,
    )

    return response, 200


@auth_bp.route("/refresh", methods = ["POST"])
@limiter.limit("30 per minute")
def refresh():
    #fetch refresh token
    refresh_raw = request.cookies.get("refresh_token")
    if not refresh_raw:
        return jsonify({"error": "no refresh token"}), 401

    refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()

    #compare with the DB stored refresh tokens
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT id, user_id, expires_at FROM refresh_tokens WHERE token_hash = %s",
            (refresh_hash,),
        )
        row = cur.fetchone()

        #no match -> invalid refresh token
        if row is None:
            return jsonify({"error": "invalid refresh token"}), 401

        token_id, user_id, expires_at = row

        #check for expiration, if expired, delete
        now = datetime.datetime.now(datetime.timezone.utc)
        if expires_at < now:
            cur.execute("DELETE FROM refresh_tokens WHERE id = %s", (token_id,))
            return jsonify({"error": "refresh token expired"}), 401

        # Rotate refresh token after token validations
        cur.execute("DELETE FROM refresh_tokens WHERE id = %s", (token_id,))

    # Generate a new refresh token
    new_refresh_raw = generate_refresh_token(user_id)

    payload = {
        "user_id": str(user_id),
        "exp": now + datetime.timedelta(minutes = 15),
    }

    access_token = jwt.encode(payload, JWT_SECRET, algorithm = "HS256")
    response = jsonify({"ok": True})
    response.set_cookie("access_token", access_token, httponly=True, samesite="Lax",
                        secure=COOKIE_SECURE, domain=COOKIE_DOMAIN, max_age=15 * 60)
    response.set_cookie("refresh_token", new_refresh_raw, httponly=True, samesite="Lax",
                        secure=COOKIE_SECURE, domain=COOKIE_DOMAIN,
                        max_age=30 * 24 * 60 * 60)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    refresh_raw = request.cookies.get("refresh_token")

    if refresh_raw:
        refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (refresh_hash,))

    response = jsonify({"ok": True})
    # the domain has to match the one they were set with, or the browser keeps them
    response.delete_cookie("access_token", domain=COOKIE_DOMAIN)
    response.delete_cookie("refresh_token", domain=COOKIE_DOMAIN)
    return response, 200


def generate_refresh_token(user_id):
    refresh_raw = secrets.token_urlsafe(32)
    new_refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
    new_refresh_expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM refresh_tokens WHERE expires_at < now()"
        )
        cur.execute(
            "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, new_refresh_hash, new_refresh_expires),
        )

    return refresh_raw
