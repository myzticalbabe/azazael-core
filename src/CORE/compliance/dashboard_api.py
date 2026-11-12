"""
Compliance Dashboard API — Flask server serving compliance_history.db data.

Usage:
    python dashboard_api.py
    # Opens at http://localhost:5000

Endpoints:
    GET /api/summary       — Latest scan summary
    GET /api/history       — All scan history (7 days)
    GET /api/trend         — Trend analysis (30 days)
    GET /api/regressions   — Regression detection (7 days)
    GET /                  — Dashboard HTML (Chart.js)
"""

import sys
import os
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def create_app():
    from flask import Flask, jsonify, send_from_directory
    from CORE.compliance.scanner_db import FindingsDB

    app = Flask(__name__)
    db = FindingsDB()

    @app.route("/api/summary")
    def api_summary():
        return jsonify(db.get_summary())

    @app.route("/api/history")
    def api_history():
        return jsonify(db.get_history(days=7))

    @app.route("/api/trend")
    def api_trend():
        return jsonify(db.get_trend(days=30))

    @app.route("/api/regressions")
    def api_regressions():
        return jsonify(db.get_regressions(days=7))

    @app.route("/")
    def index():
        return send_from_directory(
            os.path.dirname(__file__), "dashboard.html"
        )

    return app


if __name__ == "__main__":
    app = create_app()
    print("  Compliance Dashboard: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
