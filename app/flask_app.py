from __future__ import annotations

import os

from flask import Flask, redirect, session, url_for

from core.config import AppConfig
from core.state import AppStateManager
from routes.auth_routes import create_auth_blueprint
from routes.profile_routes import create_profile_blueprint
from routes.routes import create_routes_blueprint
from services.face_service import render_markdown_as_html
from services.staff_service import save_profile_image


def create_flask_app(config: AppConfig, state: AppStateManager) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "face-recognition-kiosk-dev-secret")

    deps = {
        "db_path": config.db_path,
        "base_save_dir": config.base_save_dir,
        "get_thresholds": state.get_thresholds,
        "set_thresholds": state.set_thresholds,
        "get_user_count": lambda: state.user_count,
        "reset_database_state": state.reset_database_state,
        "reset_registration_state": state.reset_registration_state,
        "remove_user_embedding": state.remove_user,
        "render_markdown_as_html": render_markdown_as_html,
    }

    app.register_blueprint(create_routes_blueprint(deps))
    app.register_blueprint(create_auth_blueprint())
    app.register_blueprint(create_profile_blueprint(save_profile_image))

    @app.route("/")
    def index():
        if "staff_id" in session:
            return redirect(url_for("routes.dashboard_page"))
        return redirect(url_for("auth_routes.auth_login"))

    @app.route("/register")
    @app.route("/kiosk")
    @app.route("/kiosk-improved")
    def spa_public_routes():
        return app.send_static_file("react/index.html")

    return app
