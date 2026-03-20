import os
from flask import Blueprint, redirect, request, session, url_for, jsonify, send_from_directory, current_app

from auth import login_user, logout_user
from services.staff_service import apply_login_session


def create_auth_blueprint():
    bp = Blueprint("auth_routes", __name__)

    @bp.route("/login", methods=["GET"], endpoint="auth_login")
    @bp.route("/login", methods=["GET"])
    def auth_login():
        if "staff_id" in session:
            return redirect(url_for("routes.dashboard_page"))
        return send_from_directory(os.path.join(current_app.static_folder, "react"), "index.html")

    @bp.route("/api/login", methods=["POST"], endpoint="api_login")
    def api_login():
        payload = request.get_json(silent=True) or {}
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""

        user, error = login_user(username, password)
        if not user:
            return jsonify({"authenticated": False, "message": error or "Login failed"}), 401

        apply_login_session(session, user)
        return jsonify(
            {
                "authenticated": True,
                "staff_id": user.get("staff_id"),
                "username": user.get("username"),
                "full_name": user.get("full_name"),
                "role": user.get("role"),
                "profile_image": user.get("profile_image"),
            }
        )

    @bp.route("/logout", methods=["POST"], endpoint="auth_logout")
    @bp.route("/logout", methods=["POST"])
    def auth_logout():
        logout_user()
        return redirect(url_for("auth_routes.auth_login"))

    @bp.route("/api/logout", methods=["POST"], endpoint="api_logout")
    def api_logout():
        logout_user()
        return jsonify({"success": True})

    @bp.route("/api/session", methods=["GET"], endpoint="api_session")
    def api_session():
        if "staff_id" not in session:
            return jsonify({"authenticated": False})
        return jsonify(
            {
                "authenticated": True,
                "staff_id": session.get("staff_id"),
                "username": session.get("username"),
                "full_name": session.get("full_name"),
                "role": session.get("role"),
                "profile_image": session.get("profile_image"),
            }
        )

    @bp.route("/unauthorized", endpoint="unauthorized")
    def unauthorized():
        if "staff_id" not in session:
            return redirect(url_for("auth_routes.auth_login"))
        return send_from_directory(os.path.join(current_app.static_folder, "react"), "index.html"), 403

    return bp
