import time
from uuid import uuid4

from flask import Flask, g, has_request_context, jsonify, request
from routes.auth import auth_bp
from routes.jobs import jobs_bp
from routes.resume import resume_bp
from routes.tailoring import tailoring_bp
from extensions import limiter
from commands import (
    register_commands,
    register_grounding_report,
    register_run_sweep,
    register_relation_maintenance,
    register_tailoring_worker,
    register_rewrite_approvals,
    register_skill_relations,
    register_usage_report,
)
from config import env_str
from db import check_schema
from services.maintenance import start as start_maintenance
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(request_id)s] %(message)s",
)


# a record factory rather than a handler filter, so the id is on the record itself and
# every logger gets it — including the service modules that know nothing about requests
_base_factory = logging.getLogRecordFactory()


def _with_request_id(*args, **kwargs):
    record = _base_factory(*args, **kwargs)
    record.request_id = getattr(g, "request_id", "-") if has_request_context() else "-"
    return record


logging.setLogRecordFactory(_with_request_id)
# Errors have to land somewhere a person will look. Without this a 500 in production exists
# only in whatever the platform kept of stdout, and the first signal is a user complaining.
# No DSN set (local, CI) means the SDK is never initialised and nothing is sent.
SENTRY_DSN = env_str("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env_str("APP_ENV", "development"),
        # exception reports only. Traces are a paid-plan concern and this is a free tier.
        traces_sample_rate=0,
        # resumes, job descriptions and answers are the user's own writing, and none of it
        # belongs in a third-party error tracker
        send_default_pii=False,
    )

app = Flask(__name__)

# Match the private resume bucket limit; Flask rejects larger bodies first.
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

limiter.init_app(app) # connect limiter to app

logger = logging.getLogger(__name__)

app.register_blueprint(auth_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(resume_bp)
app.register_blueprint(tailoring_bp)
register_commands(app)
register_usage_report(app)
register_run_sweep(app)
register_tailoring_worker(app)     # the worker service's entire entry point
register_skill_relations(app)
register_rewrite_approvals(app)
register_relation_maintenance(app)
register_grounding_report(app)

check_schema()

# abandoned runs are otherwise only noticed when someone opens that run's page
start_maintenance(app)

# Who may call this API from a browser. Empty locally: the Vite proxy makes the frontend
# same-origin, so no CORS headers are needed and none are sent. In production the frontend is
# a different host (app.* on Vercel, api.* on Railway) and every authenticated request fails
# without these — a blank page and a console error, with nothing in the server log.
CORS_ORIGINS = {
    origin.strip() for origin in env_str("CORS_ORIGINS").split(",")
    if origin.strip()
}


@app.before_request
def preflight():
    """Answer the browser's OPTIONS probe before auth can reject it.

    A preflight carries no cookies, so letting it reach a @require_auth route would 401 the
    check that decides whether the real request is allowed to happen.
    """
    if request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method"):
        return _allow(app.make_default_options_response(), request.headers.get("Origin"))


def _allow(response, origin):
    """Grant one specific origin, never a wildcard.

    `Allow-Credentials` and `Allow-Origin: *` are mutually exclusive by spec — cookies need a
    named origin. `Vary: Origin` so a cache never serves one origin's grant to another.
    """
    if origin and origin in CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Idempotency-Key"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers.add("Vary", "Origin")
    return response


@app.before_request
def start_request():
    g.request_id = uuid4().hex[:8]
    g.started_at = time.perf_counter()


@app.after_request
def allow_origin(response):
    return _allow(response, request.headers.get("Origin"))


@app.after_request
def log_request(response):
    if request.path.startswith("/api"):
        logger.info(
            "%s %s %d %.0fms user=%s",
            request.method, request.path, response.status_code,
            (time.perf_counter() - getattr(g, "started_at", time.perf_counter())) * 1000,
            getattr(g, "user_id", "-"),
        )
    return response


@app.route("/api/health")
def health():
    """Liveness: is this process running. Deliberately touches nothing else.

    A liveness probe that checks a dependency restarts healthy processes in a loop whenever
    that dependency has a bad minute. Whether this server should receive traffic is a
    different question, and it is `/api/ready`'s.
    """
    return jsonify({"status": "alive"})


@app.route("/api/version")
def version():
    """Which commit is actually serving.

    Three features in a row looked broken when they were only undeployed, and each cost a
    round of guessing. A build id turns "is my fix live?" into one request. Railway injects
    the SHA; the short form is a build identifier, not information worth hiding.
    """
    return jsonify({
        "commit": env_str("RAILWAY_GIT_COMMIT_SHA", "unknown")[:7],
        "environment": env_str("APP_ENV", "development"),
    })


@app.route("/api/ready")
def ready():
    """Readiness: should this process receive traffic.

    503 for either failure, because both mean requests will break: the database is
    unreachable, or it is behind the schema this code expects. A deploy that half-applied its
    DDL used to pass its health check and serve 500s; this is what stops that.
    """
    state = check_schema()
    if not state.reachable:
        return jsonify({"status": "unreachable", "detail": "database is not reachable"}), 503
    if state.missing:
        return jsonify({"status": "behind", "missing": state.missing}), 503
    return jsonify({"status": "ready"})


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "file too large (5 MB max)"}), 413


if __name__ == "__main__":
    app.run(debug=True, port=5001)
