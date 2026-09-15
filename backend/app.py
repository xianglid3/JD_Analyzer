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
    register_rewrite_approvals,
    register_skill_relations,
    register_usage_report,
)
from db import check_schema
from services.maintenance import start as start_maintenance
import logging

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
register_skill_relations(app)
register_rewrite_approvals(app)
register_relation_maintenance(app)
register_grounding_report(app)

check_schema()

# abandoned runs are otherwise only noticed when someone opens that run's page
start_maintenance(app)

@app.before_request
def start_request():
    g.request_id = uuid4().hex[:8]
    g.started_at = time.perf_counter()


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
