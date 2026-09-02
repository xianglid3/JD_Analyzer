import os

from flask import g, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# In-memory counters are per-process and reset on restart, so they only bound abuse on a
# single worker. Point RATELIMIT_STORAGE_URI at Redis in production, or run one worker and
# accept the limit as approximate. A spend quota needs a database row, not this.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=["240 per hour"],
)


def authenticated_user_key():
    """Rate-limit an authenticated route by account instead of shared IP."""
    return str(g.user_id)


def credential_key():
    """IP plus the username being tried.

    Keying on IP alone lets an attacker cycle addresses against one account; keying on the
    username alone lets one attacker lock everyone out of it. Both together limits the pair.
    """
    data = request.get_json(silent=True)
    username = ""
    if isinstance(data, dict) and isinstance(data.get("username"), str):
        username = data["username"].strip().lower()[:50]
    return f"{get_remote_address()}:{username}"
