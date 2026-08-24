from flask import Flask, jsonify
from routes.auth import auth_bp
from routes.jobs import jobs_bp
from routes.resume import resume_bp
from extensions import limiter
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)

# cap request bodies at 2 MB — Flask returns 413 before any handler runs
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

limiter.init_app(app) # connect limiter to app

app.register_blueprint(auth_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(resume_bp)

@app.route("/api/health")
def health():
    return jsonify({"status": "alive"})


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "file too large (2 MB max)"}), 413


if __name__ == "__main__":
    app.run(debug=True, port=5001)
