from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, redirect, session, url_for

from core.config import AppConfig
from core.state import AppStateManager
from database.repository import UserRepository
from routes.auth_routes import create_auth_blueprint
from routes.profile_routes import create_profile_blueprint
from routes.routes import create_routes_blueprint
from services.embedding_service import EmbeddingService
from services.face_service import render_markdown_as_html
from services.quality_service import FaceQualityService
from services.staff_service import save_profile_image


def create_flask_app(config: AppConfig, state: AppStateManager, repository: UserRepository) -> Flask:
    repo_root = Path(__file__).resolve().parent.parent
    static_root = repo_root / "static"
    app = Flask(__name__, static_folder=str(static_root), static_url_path="/static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "face-recognition-kiosk-dev-secret")

    deps = {
        "config": config,
        "db_path": config.db_path,
        "base_save_dir": config.base_save_dir,
        "repository": repository,
        "embedding_service": EmbeddingService(config),
        "quality_service": FaceQualityService(config),
        "get_thresholds": state.get_thresholds,
        "set_thresholds": state.set_thresholds,
        "get_user_count": lambda: state.user_count,
        "get_registration_state": lambda: state.registration_state,
        "capture_registration_sample": state.capture_registration_sample,
        "reset_database_state": state.reset_database_state,
        "reset_registration_state": state.reset_registration_state,
        "complete_registration": state.complete_registration,
        "remove_user_embedding": state.remove_user,
        "replace_user": state.replace_user,
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
    def spa_public_routes():
        return app.send_static_file("react/index.html")

    return app
