from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, redirect, request, session, url_for

from core.config import AppConfig
from core.state import AppStateManager
from database.repository import UserRepository
from app.realtime import socketio
from routes.auth_routes import create_auth_blueprint
from routes.alert_routes import bp as alerts_bp
from routes.internal_routes import create_internal_blueprint
from routes.occupancy_routes import bp as occupancy_bp
from routes.profile_routes import create_profile_blueprint
from routes.routes import create_routes_blueprint
from services.embedding_service import EmbeddingService
from services.face_service import render_markdown_as_html
from services.quality_service import FaceQualityService
from services.staff_service import save_profile_image


def create_flask_app(config: AppConfig, state: AppStateManager, repository: UserRepository, cli=None) -> Flask:
    repo_root = Path(__file__).resolve().parent.parent
    static_root = repo_root / "static"
    app = Flask(__name__, static_folder=str(static_root), static_url_path="/static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "face-recognition-kiosk-dev-secret")

    deps = {
        "config": config,
        "db_path": config.db_path,
        "base_save_dir": config.base_save_dir,
        "repository": repository,
        "worker_runtime_attached": bool(cli),
        "embedding_service": EmbeddingService(config),
        "quality_service": FaceQualityService(config),
        "get_thresholds": state.get_thresholds,
        "set_thresholds": state.set_thresholds,
        "get_user_count": lambda: state.user_count,
        "get_registration_state": lambda: state.registration_state,
        "capture_registration_sample": state.capture_registration_sample,
        "get_current_registration_pose": state.get_current_registration_pose,
        "get_registration_progress": state.get_registration_progress,
        "is_registration_ready": state.is_registration_ready,
        "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
        "reset_database_state": state.reset_database_state,
        "reset_registration_state": state.reset_registration_state,
        "start_web_registration_session": state.start_web_registration_session,
        "cancel_web_registration_session": state.cancel_web_registration_session,
        "set_registration_status_reason": state.set_registration_status_reason,
        "clear_registration_status_reason": state.clear_registration_status_reason,
        "complete_registration": state.complete_registration,
        "remove_user_embedding": state.remove_user,
        "replace_user": state.replace_user,
        "render_markdown_as_html": render_markdown_as_html,
        "yolo_model": cli.yolo_model if cli else None,
        "yolo_device": cli.yolo_device if cli else "cpu",
        "pause_detection": cli.pause_detection if cli else (lambda: None),
        "resume_detection": cli.resume_detection if cli else (lambda: None),
        "detection_paused": cli.detection_paused if cli else (lambda: False),
        "stream_status": cli.get_stream_status
        if cli
        else (lambda: {"state": "unknown", "message": "Camera status unavailable."}),
    }

    app.register_blueprint(create_routes_blueprint(deps))
    app.register_blueprint(create_auth_blueprint())
    app.register_blueprint(create_profile_blueprint(save_profile_image))
    app.register_blueprint(create_internal_blueprint(deps))
    app.register_blueprint(occupancy_bp, url_prefix="/api/occupancy")
    app.register_blueprint(alerts_bp, url_prefix="/api/alerts")
    socketio.init_app(app)

    @app.route("/")
    def index():
        if "staff_id" in session:
            return redirect(url_for("routes.dashboard_page"))
        return redirect(url_for("auth_routes.auth_login"))

    @app.route("/register")
    def spa_public_routes():
        if "staff_id" not in session:
            return redirect(url_for("auth_routes.auth_login", next=request.path))
        if session.get("role") not in {"super_admin", "library_admin", "library_staff"}:
            return redirect(url_for("auth_routes.unauthorized"))
        return app.send_static_file("react/index.html")

    return app
