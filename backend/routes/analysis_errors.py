import json

from flask import jsonify
from openai import APITimeoutError, APIConnectionError, RateLimitError, APIStatusError
from pydantic import ValidationError


def analysis_error_response(exc, logger):
    """Translate analysis failures into consistent HTTP responses."""
    if isinstance(exc, APITimeoutError):
        logger.warning("OpenAI request timed out")
        return jsonify({"error": "analysis timed out, please try again"}), 504

    if isinstance(exc, RateLimitError):
        logger.warning("OpenAI rate limit request_id=%s", exc.request_id)
        return jsonify({"error": "analysis service is busy, please try again"}), 503

    if isinstance(exc, APIConnectionError):
        logger.exception("Could not connect to OpenAI")
        return jsonify({"error": "analysis service unavailable"}), 503

    if isinstance(exc, (json.JSONDecodeError, ValidationError)):
        logger.exception("OpenAI returned invalid structured output")
        return jsonify({"error": "analysis returned an invalid result"}), 502

    if isinstance(exc, APIStatusError):
        logger.exception("OpenAI API error request_id=%s", exc.request_id)
        return jsonify({"error": "analysis service failed"}), 502

    logger.exception("Unexpected analysis failure")
    return jsonify({"error": "internal server error"}), 500
