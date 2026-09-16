"""Reading configuration out of the environment without crashing on a blank line.

`MAX_SIGNUPS_PER_DAY=` in a .env file is not zero — it is the string "", and `int("")` raises
at import time, which takes the whole app down before it can log anything useful. A blank
value means "not set", the same as an absent one, because that is what someone writing a .env
means by it.
"""

import os


def env_str(name, default=""):
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def env_int(name, default):
    raw = env_str(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number, not {raw!r}") from None


def env_float(name, default):
    raw = env_str(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        raise RuntimeError(f"{name} must be a number, not {raw!r}") from None


def env_flag(name, default=False):
    raw = env_str(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")
