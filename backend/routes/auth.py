from flask import Blueprint, request, jsonify, g
from db import get_connection
from middleware import require_auth
import psycopg2
import bcrypt
import secrets
import hashlib
import jwt
import datetime
import os

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
JWT_SECRET = os.environ["JWT_SECRET"]


@auth_bp.route("/me", methods = ["GET"])
@require_auth
def me():
    connection = get_connection()
    cur = connection.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = %s", (g.user_id,))
    row = cur.fetchone()
    cur.close()
    connection.close()

    if row is None:
        return jsonify({"error": "user not found"}), 404
    
    user_id, username = row
    return jsonify({"id": str(user_id), "username": username}),200


@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "username or password required"}), 400   

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    connection = get_connection()
    cur = connection.cursor()
    
    try:
        cur.execute(
            "INSERT INTO users(username, password_hash) VALUES (%s, %s) RETURNING id",(username, password_hash),
        )
        user_id = cur.fetchone()[0]
        connection.commit()

    except psycopg2.errors.UniqueViolation:
        connection.rollback()
        return jsonify({"error": "username already taken"}), 409

    finally:
        cur.close()
        connection.close()

    return jsonify({"id": user_id, "username": username}), 201


@auth_bp.route("/login", methods =["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"error": "username or password required"}), 400   
    
    connection = get_connection()
    cur = connection.cursor()
    cur.execute(
        "SELECT id, password_hash From users Where username = %s", (username,),
    )

    row = cur.fetchone()
    cur.close()
    connection.close()

    if row is None:
        return jsonify({"error": "invalid credentials"}), 401
    
    user_id, password_hash = row
    
    if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
        return jsonify({"error": "invalid credentails"}), 401

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
        secure=False,
        max_age=15 * 60,
    )

    # refresh token via hashed string
    refresh_raw = generate_refresh_token(user_id)

    response.set_cookie(
        "refresh_token",
        refresh_raw,
        httponly=True,
        samesite="Lax",
        secure=False,
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
    connection = get_connection()
    cur = connection.cursor()
    cur.execute(
        "SELECT id, user_id, expires_at FROM refresh_tokens WHERE token_hash = %s",
        (refresh_hash,),
    )
    row = cur.fetchone()

    #no match -> invalid refresh token 
    if row is None:
        cur.close()
        connection.close()
        return jsonify({"error": "invalid refresh token"}), 401

    token_id, user_id, expires_at = row

    #check for expiration, if expired, delete
    now = datetime.datetime.now(datetime.timezone.utc)
    if expires_at < now:
        cur.execute("DELETE FROM refresh_tokens WHERE id = %s", (token_id,))
        connection.commit()
        cur.close()
        connection.close()
        return jsonify({"error": "refresh token expired"}), 401

    # Rotate refresh token after token validations
    cur.execute("DELETE FROM refresh_tokens WHERE id = %s", (token_id,))
    connection.commit()
    cur.close()
    connection.close()

    # Generate a new refresh token
    new_refresh_raw = generate_refresh_token(user_id)

    payload = {
        "user_id": str(user_id),
        "exp": now + datetime.timedelta(minutes = 15),
    }

    access_token = jwt.encode(payload, JWT_SECRET, algorithm = "HS256")
    response = jsonify({"ok": True})
    response.set_cookie("access_token", access_token, httponly=True, samesite="Lax", secure=False, max_age=15 * 60)
    response.set_cookie("refresh_token", new_refresh_raw, httponly=True, samesite="Lax", secure=False, max_age=30 * 24 * 60 * 60)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    refresh_raw = request.cookies.get("refresh_token")

    if refresh_raw:
        refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
        connection = get_connection()
        cur = connection.cursor()
        cur.execute("DELETE FROM refresh_tokens WHERE token_hash = %s", (refresh_hash,))
        connection.commit()
        cur.close()
        connection.close()

    response = jsonify({"ok": True})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response, 200


def generate_refresh_token(user_id):
    connection = get_connection()
    cur = connection.cursor()
    
    refresh_raw = secrets.token_urlsafe(32)
    new_refresh_hash = hashlib.sha256(refresh_raw.encode("utf-8")).hexdigest()
    new_refresh_expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    
    cur.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user_id, new_refresh_hash, new_refresh_expires),
    )
    connection.commit()
    cur.close()
    connection.close()

    return refresh_raw


