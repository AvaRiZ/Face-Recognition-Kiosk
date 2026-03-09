from flask import Blueprint, redirect, render_template, request, session, url_for

from auth import login_user, logout_user
from services.staff_service import apply_login_session


def create_auth_blueprint():
    bp = Blueprint("auth_routes", __name__)

    @bp.route("/admin/login", methods=["GET", "POST"], endpoint="auth_login")
    @bp.route("/login", methods=["GET", "POST"])
    def auth_login():
        if "staff_id" in session:
            return redirect(url_for("admin_routes.dashboard_page"))

        error = None
        next_url = request.args.get("next", "/admin/dashboard")
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            user, error = login_user(username, password)
            if user:
                apply_login_session(session, user)
                redirect_target = request.form.get("next") or "/admin/dashboard"
                if not redirect_target.startswith("/"):
                    redirect_target = "/"
                return redirect(redirect_target)

        return render_template("html/auth/login.html", error=error, next_url=next_url)

    @bp.route("/admin/logout", methods=["POST"], endpoint="auth_logout")
    @bp.route("/logout", methods=["POST"])
    def auth_logout():
        logout_user()
        return redirect(url_for("auth_routes.auth_login"))

    @bp.route("/unauthorized", endpoint="unauthorized")
    def unauthorized():
        if "staff_id" not in session:
            return redirect(url_for("auth_routes.auth_login"))
        return render_template("html/errors/403.html"), 403

    return bp
