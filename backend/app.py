from flask import Flask, jsonify
from routes.auth import auth_bp
from routes.jobs import jobs_bp
from routes.resume import resume_bp

from extensions import limiter
app = Flask(__name__)

limiter.init_app(app) # connect limiter to app

app.register_blueprint(auth_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(resume_bp)

@app.route("/api/health")
def health():
    return jsonify({"status": "alive"})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
