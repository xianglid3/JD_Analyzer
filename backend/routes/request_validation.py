from flask import jsonify, request


def get_json_object():
    """Return a JSON object or a consistent Flask 400 response."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "JSON object required"}), 400)
    return data, None
