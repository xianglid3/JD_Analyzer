from functools import wraps
from flask import request, jsonify, g
import jwt
import os

JWT_SECRET = os.environ["JWT_SECRET"]

# building @require_auth 
def require_auth(f):
    @wraps(f)                    #?
    def wrapper(*args, **kwargs):
        token = request.cookies.get("access_token")
        if not token:
            return jsonify({"error": "not logged in"}), 401
        
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms = ["HS256"])

        except jwt.ExpiredSignatureError:
            return jsonify({"error": "session expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "invalid token"}), 401

        g.user_id = payload["user_id"]
        return f(*args, **kwargs)

    return wrapper