from urllib.parse import urlsplit, urlunsplit

from flask import jsonify, request


MAX_SOURCE_URL_CHARS = 2048


def get_json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "JSON object required"}), 400)
    return data, None


def normalize_optional_http_url(value):
    error = "source_url must be a valid HTTP or HTTPS URL"

    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, error

    value = value.strip()
    if not value:
        return None, None
    if len(value) > MAX_SOURCE_URL_CHARS:
        return None, "source_url must be 2048 characters or fewer"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None, error

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None, error

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None, error
    if parsed.username is not None or parsed.password is not None:
        return None, error

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"

    normalized = urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))
    return normalized, None
