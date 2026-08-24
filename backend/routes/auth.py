from flask import Blueprint, request, jsonify, g
from db import get_cursor
from middleware import require_auth
import psycopg2
import bcrypt
import secrets
import hashlib
import jwt
import datetime
import os
import re

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
JWT_SECRET = os.environ["JWT_SECRET"]
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

#validation limits
MIN_USERNAME_CHARS = 3
MAX_USERNAME_CHARS = 50
MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

def validate_credentials(data):
    if not isinstance(data, dict):
        return None, None, "JSON body required"

    username = data.get("username")
    password = data.get("password")

    if not isinstance(username, str) or not isinstance(password, str):
        return None, None, "username and password required"

    username = username.strip()
    password_bytes = password.encode("utf-8")

    if not MIN_USERNAME_CHARS <= len(username) <= MAX_USERNAME_CHARS:
        return None, None, "username must be 3–50 characters"

    if not USERNAME_PATTERN.fullmatch(username):
        return None, None, "username can only contain letters and numbers"

    if any(char.isspace() for char in password):
        return None, None, "password cannot contain whitespace"

    if not MIN_PASSWORD_BYTES <= len(password_bytes) <= MAX_PASSWORD_BYTES:
        return None, None, "password must be 8–72 bytes"

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


@auth_bp.route("/signup", methods=["POST"])
def signup():

    # silent=true prevents bad JSON causing flask error before validation run
    data = request.get_json(silent=True)
    username, password, error = validate_credentials(data)

    if error:
        return jsonify({"error": error}), 400


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
        max_age=30 * 24 * 60 * 60,
    )

    return response, 200


@auth_bp.route("/refresh", methods = ["POST"])
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
    response.set_cookie("access_token", access_token, httponly=True, samesite="Lax", secure=COOKIE_SECURE, max_age=15 * 60)
    response.set_cookie("refresh_token", new_refresh_raw, httponly=True, samesite="Lax", secure=COOKIE_SECURE, max_age=30 * 24 * 60 * 60)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    refresh_raw = request.cookies.get("refresh_token")

    if refresh_raw:
        refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (refresh_hash,))

    response = jsonify({"ok": True})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
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
