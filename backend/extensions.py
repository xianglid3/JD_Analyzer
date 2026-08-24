from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def authenticated_user_key():
    """Rate-limit an authenticated route by account instead of shared IP."""
    return str(g.user_id)
