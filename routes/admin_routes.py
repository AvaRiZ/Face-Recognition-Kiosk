import os
import shutil
import sqlite3
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, session, url_for, current_app

from auth import (
    create_staff,
    get_all_staff,
    log_action,
    login_required,
    role_required,
    toggle_staff_status,
)


def create_admin_blueprint(deps):
    bp = Blueprint("admin_routes", __name__)

    @bp.route("/admin", endpoint="pages_home")
    @login_required
    @role_required("super_admin", "library_admin")
    def pages_home():
        return render_template("html/pages/admin-home.html")

    @bp.route("/admin/policy", endpoint="policy_page")
    @login_required
    @role_required("super_admin", "library_admin")
    def policy_page():
        policy_html = deps["render_markdown_as_html"](Path("static/content/markdown/policy.md"))
        return render_template("html/policy.html", policy=policy_html)

    @bp.route("/admin/dashboard", endpoint="dashboard_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def dashboard_page():
        return render_template("html/pages/admin-dashboard.html", user_count=deps["get_user_count"]())

    @bp.route("/admin/routes", endpoint="route_list_page")
    @login_required
    @role_required("super_admin", "library_admin")
    def route_list_page():
        routes = []
        for i, rule in enumerate(sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule), start=1):
            routes.append(
                {
                    "i": i,
                    "uri": rule.rule,
                    "name": rule.endpoint,
                    "action": rule.endpoint,
                    "methods": sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}),
                }
            )
        return render_template("html/pages/route-list/index.html", routes=routes)

    @bp.route("/admin/manage-users", endpoint="manage_users")
    @login_required
    @role_required("super_admin")
    def manage_users():
        staff_rows = get_all_staff()
        return render_template("html/pages/manage-users.html", staff_rows=staff_rows)

    @bp.route("/admin/manage-users/create", methods=["POST"], endpoint="manage_users_create")
    @login_required
    @role_required("super_admin")
    def manage_users_create():
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "").strip()

        allowed_roles = {"library_admin", "library_staff"}
        if not full_name or not username or not password:
            flash("Full name, username, and password are required.", "error")
            return redirect(url_for("admin_routes.manage_users"))

        if role not in allowed_roles:
            flash("Invalid role. Only Admin or Staff can be created here.", "error")
            return redirect(url_for("admin_routes.manage_users"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("admin_routes.manage_users"))

        success, message = create_staff(username, password, full_name, role)
        if success:
            log_action("CREATE_STAFF", target=username)
            flash(f"User '{username}' created successfully.", "success")
        else:
            flash(message, "error")

        return redirect(url_for("admin_routes.manage_users"))

    @bp.route("/admin/manage-users/toggle/<int:staff_id>", methods=["POST"], endpoint="manage_users_toggle")
    @login_required
    @role_required("super_admin")
    def manage_users_toggle(staff_id):
        if staff_id == session.get("staff_id"):
            flash("You cannot deactivate your own account.", "error")
            return redirect(url_for("admin_routes.manage_users"))

        toggle_staff_status(staff_id)
        log_action("TOGGLE_STAFF_STATUS", target=str(staff_id))
        flash("User status updated.", "success")
        return redirect(url_for("admin_routes.manage_users"))

    @bp.route("/admin/settings", methods=["GET", "POST"], endpoint="settings")
    @login_required
    @role_required("super_admin")
    def settings():
        if request.method == "POST":
            deps["set_thresholds"](
                float(request.form.get("threshold", deps["get_thresholds"]()[0])),
                request.form.get("adaptive_threshold") == "on",
                float(request.form.get("quality_threshold", deps["get_thresholds"]()[2])),
            )
            return redirect(url_for("admin_routes.settings"))

        threshold, adaptive_threshold, quality_threshold = deps["get_thresholds"]()
        return render_template(
            "html/settings.html",
            threshold=threshold,
            adaptive_threshold=adaptive_threshold,
            quality_threshold=quality_threshold,
            user_count=deps["get_user_count"](),
        )

    @bp.route("/admin/api/stats", endpoint="get_stats")
    @login_required
    @role_required("super_admin", "library_admin")
    def get_stats():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT u.name, COUNT(r.log_id) as recognitions,
                   AVG(r.confidence) as avg_confidence
            FROM users u
            LEFT JOIN recognition_log r ON u.user_id = r.user_id
            GROUP BY u.user_id
            ORDER BY recognitions DESC
            """
        )
        stats = c.fetchall()
        conn.close()

        return {
            "user_count": deps["get_user_count"](),
            "recognition_stats": [
                {"name": name, "recognitions": rec_count, "avg_confidence": avg_conf}
                for name, rec_count, avg_conf in stats
            ],
        }

    @bp.route("/admin/registered-profiles", endpoint="registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, course, created_at, last_updated
            FROM users
            ORDER BY created_at DESC
            """
        )
        profile_rows = c.fetchall()
        conn.close()

        return render_template("html/pages/registered-profiles.html", profile_rows=profile_rows)

    @bp.route("/admin/api/reset_database", methods=["POST"], endpoint="reset_database")
    @login_required
    @role_required("super_admin", "library_admin")
    def reset_database():
        try:
            conn = sqlite3.connect(deps["db_path"])
            c = conn.cursor()
            c.execute("DELETE FROM users")
            c.execute("DELETE FROM recognition_log")
            conn.commit()
            conn.close()

            if os.path.exists(deps["base_save_dir"]):
                shutil.rmtree(deps["base_save_dir"])

            deps["reset_database_state"]()
            return {"success": True, "message": "Database reset successfully"}
        except Exception as e:
            return {"success": False, "message": str(e)}, 500

    @bp.route("/admin/api/clear_log", methods=["POST"], endpoint="clear_log")
    @login_required
    @role_required("super_admin", "library_admin")
    def clear_log():
        try:
            conn = sqlite3.connect(deps["db_path"])
            c = conn.cursor()
            c.execute("DELETE FROM recognition_log")
            conn.commit()
            conn.close()
            return {"success": True, "message": "Recognition log cleared"}
        except Exception as e:
            return {"success": False, "message": str(e)}, 500

    @bp.route("/admin/api/reset_registration", methods=["POST"], endpoint="reset_registration")
    @login_required
    @role_required("super_admin", "library_admin")
    def reset_registration():
        deps["reset_registration_state"]()
        return {"success": True, "message": "Registration state reset"}

    return bp
