import json
from unittest.mock import Mock

import httpx
from flask import Flask
from openai import APITimeoutError, APIConnectionError, RateLimitError, APIStatusError
from pydantic import ValidationError

from routes.analysis_errors import analysis_error_response
from services.openai_services import ResumeExtraction


app = Flask(__name__)
request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def assert_mapping(exc, expected_status, expected_message):
    logger = Mock()
    with app.app_context():
        response, status = analysis_error_response(exc, logger)

    assert status == expected_status
    assert response.get_json() == {"error": expected_message}


def test_timeout_maps_to_504():
    assert_mapping(
        APITimeoutError(request=request),
        504,
        "analysis timed out, please try again",
    )


def test_rate_limit_maps_to_503():
    response = httpx.Response(429, request=request, headers={"x-request-id": "req_rate"})
    assert_mapping(
        RateLimitError("rate limited", response=response, body=None),
        503,
        "analysis service is busy, please try again",
    )


def test_connection_failure_maps_to_503():
    assert_mapping(
        APIConnectionError(request=request),
        503,
        "analysis service unavailable",
    )


def test_invalid_json_maps_to_502():
    error = json.JSONDecodeError("invalid JSON", "not json", 0)
    assert_mapping(error, 502, "analysis returned an invalid result")


def test_pydantic_validation_failure_maps_to_502():
    try:
        ResumeExtraction(skills=[1])
    except ValidationError as error:
        assert_mapping(error, 502, "analysis returned an invalid result")
    else:
        raise AssertionError("expected ResumeExtraction validation to fail")


def test_api_status_error_maps_to_502():
    response = httpx.Response(500, request=request, headers={"x-request-id": "req_api"})
    assert_mapping(
        APIStatusError("upstream failure", response=response, body=None),
        502,
        "analysis service failed",
    )


def test_unexpected_error_maps_to_500():
    assert_mapping(RuntimeError("bug"), 500, "internal server error")
