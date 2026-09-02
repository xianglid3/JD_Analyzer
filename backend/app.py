import time
from uuid import uuid4

from flask import Flask, g, has_request_context, jsonify, request
from routes.auth import auth_bp
from routes.jobs import jobs_bp
from routes.resume import resume_bp
from routes.tailoring import tailoring_bp
from extensions import limiter
from commands import register_commands, register_run_sweep, register_usage_report
from db import check_schema
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

check_schema()

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
    return jsonify({"status": "alive"})


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "file too large (5 MB max)"}), 413


if __name__ == "__main__":
    app.run(debug=True, port=5001)
