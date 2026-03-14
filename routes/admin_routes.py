import os
import shutil
import sqlite3
import struct
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

    def _coerce_confidence(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            try:
                if len(raw) == 4:
                    return struct.unpack("f", raw)[0]
                if len(raw) == 8:
                    return struct.unpack("d", raw)[0]
            except struct.error:
                pass
            try:
                return float(raw.decode("utf-8", errors="ignore"))
            except (ValueError, TypeError):
                return None
        return None

    @bp.route("/home", endpoint="pages_home")
    @login_required
    @role_required("super_admin", "library_admin")
    def pages_home():
        return render_template("html/pages/admin-home.html")

    @bp.route("/policy", endpoint="policy_page")
    @login_required
    @role_required("super_admin", "library_admin")
    def policy_page():
        policy_html = deps["render_markdown_as_html"](Path("static/content/markdown/policy.md"))
        return render_template("html/policy.html", policy=policy_html)

    @bp.route("/dashboard", endpoint="dashboard_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def dashboard_page():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM recognition_log")
        total_logs = c.fetchone()[0]
        c.execute(
            """
            SELECT COUNT(*) FROM recognition_log
            WHERE DATE(timestamp) = DATE('now')
            """
        )
        today_logs = c.fetchone()[0]
        c.execute("SELECT AVG(confidence) FROM recognition_log")
        avg_conf = c.fetchone()[0]
        conn.close()

        avg_confidence = round((avg_conf or 0) * 100, 1)
        return render_template(
            "html/pages/admin-dashboard.html",
            user_count=deps["get_user_count"](),
            total_logs=total_logs,
            today_logs=today_logs,
            avg_confidence=avg_confidence,
        )

    @bp.route("/routes", endpoint="route_list_page")
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

    @bp.route("/manage-users", endpoint="manage_users")
    @login_required
    @role_required("super_admin")
    def manage_users():
        staff_rows = get_all_staff()
        return render_template("html/pages/manage-users.html", staff_rows=staff_rows)

    @bp.route("/manage-users/create", methods=["POST"], endpoint="manage_users_create")
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

    @bp.route("/manage-users/toggle/<int:staff_id>", methods=["POST"], endpoint="manage_users_toggle")
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

    @bp.route("/settings", methods=["GET", "POST"], endpoint="settings")
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

    @bp.route("/api/stats", endpoint="get_stats")
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

    @bp.route("/registered-profiles", endpoint="registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, course, created_at, last_updated
            FROM users
            WHERE archived_at IS NULL
            ORDER BY created_at DESC
            """
        )
        profile_rows = c.fetchall()
        conn.close()

        return render_template("html/pages/registered-profiles.html", profile_rows=profile_rows)

    @bp.route("/registered-profiles/archive", methods=["GET", "POST"], endpoint="registered_profiles_archive")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles_archive():
        if request.method == "POST":
            return redirect(url_for("admin_routes.registered_profiles_archive"))
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, course, created_at, last_updated
            FROM users
            WHERE archived_at IS NULL
            ORDER BY created_at DESC
            """
        )
        profile_rows = c.fetchall()
        conn.close()

        return render_template("html/pages/archive-profiles.html", profile_rows=profile_rows)

    @bp.route("/registered-profiles/archive/submit", methods=["POST"], endpoint="registered_profiles_archive_submit")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles_archive_submit():
        selected_ids = request.form.getlist("user_ids")
        if not selected_ids:
            flash("Select at least one profile to archive.", "profiles:error")
            return redirect(url_for("admin_routes.registered_profiles_archive"))

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.executemany(
            "UPDATE users SET archived_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            [(user_id,) for user_id in selected_ids],
        )
        conn.commit()
        conn.close()

        log_action("ARCHIVE_REGISTERED_PROFILES", target=",".join(selected_ids))
        flash(f"Archived {len(selected_ids)} profile(s).", "profiles:success")
        return redirect(url_for("admin_routes.registered_profiles_archive"))

    @bp.route("/archived-profiles", endpoint="archived_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def archived_profiles():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, course, created_at, last_updated, archived_at
            FROM users
            WHERE archived_at IS NOT NULL
            ORDER BY archived_at DESC
            """
        )
        profile_rows = c.fetchall()
        conn.close()

        return render_template("html/pages/archived-profiles.html", profile_rows=profile_rows)

    @bp.route("/archived-profiles/restore", methods=["POST"], endpoint="archived_profiles_restore")
    @login_required
    @role_required("super_admin", "library_admin")
    def archived_profiles_restore():
        selected_ids = request.form.getlist("user_ids")
        if not selected_ids:
            flash("Select at least one profile to restore.", "profiles:error")
            return redirect(url_for("admin_routes.archived_profiles"))

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.executemany(
            "UPDATE users SET archived_at = NULL WHERE user_id = ?",
            [(user_id,) for user_id in selected_ids],
        )
        conn.commit()
        conn.close()

        log_action("RESTORE_ARCHIVED_PROFILES", target=",".join(selected_ids))
        flash(f"Restored {len(selected_ids)} profile(s).", "profiles:success")
        return redirect(url_for("admin_routes.archived_profiles"))

    @bp.route("/api/reset_database", methods=["POST"], endpoint="reset_database")
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

    @bp.route("/api/clear_log", methods=["POST"], endpoint="clear_log")
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

    @bp.route("/api/reset_registration", methods=["POST"], endpoint="reset_registration")
    @login_required
    @role_required("super_admin", "library_admin")
    def reset_registration():
        deps["reset_registration_state"]()
        return {"success": True, "message": "Registration state reset"}
    
    
    @bp.route("/registered-profiles/delete/<int:user_id>", methods=["POST"], endpoint="delete_profile")
    @login_required
    @role_required("super_admin", "library_admin")
    def delete_profile(user_id):
        try:
            conn = sqlite3.connect(deps["db_path"])
            c = conn.cursor()
 
            # Get student info before deleting
            c.execute("SELECT name, sr_code FROM users WHERE user_id = ?", (user_id,))
            student = c.fetchone()
 
            if not student:
                conn.close()
                flash("Student not found.", "error")
                return redirect(url_for("admin_routes.registered_profiles"))
 
            name, sr_code = student
 
            # Delete recognition logs first (foreign key constraint)
            c.execute("DELETE FROM recognition_log WHERE user_id = ?", (user_id,))
 
            # Delete the student
            c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
 
            # Remove from in-memory embeddings
            deps["remove_user_embedding"](user_id)
 
            log_action("DELETE_STUDENT", target=f"{name} ({sr_code})")
            flash(f"Student '{name}' deleted successfully.", "success")
 
        except Exception as e:
            flash(f"Failed to delete student: {str(e)}", "error")
 
        return redirect(url_for("admin_routes.registered_profiles"))
 
 
    @bp.route("/entry-exit-logs", endpoint="entry_exit_logs")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def entry_exit_logs():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
 
        # All logs latest first
        c.execute("""
            SELECT u.name, u.sr_code, r.confidence, r.timestamp
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            ORDER BY r.timestamp DESC
            LIMIT 500
        """)
        raw_logs = c.fetchall()
        logs = []
        for name, sr_code, confidence, timestamp in raw_logs:
            logs.append((name, sr_code, _coerce_confidence(confidence), timestamp))
 
        conn.close()

        return render_template(
            "html/pages/entry-exit-logs.html",
            logs=logs,
        )
 
 
    @bp.route("/entry-exit-logs/export", endpoint="export_logs")
    @login_required
    @role_required("super_admin", "library_admin")
    def export_logs():
        import csv
        import io
        from datetime import date, datetime
        from flask import make_response

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        selected_date = request.args.get("date", "").strip()
        date_for_name = date.today()

        if selected_date:
            try:
                date_for_name = datetime.strptime(selected_date, "%Y-%m-%d").date()
            except ValueError:
                selected_date = ""

        query = """
            SELECT u.name, u.sr_code, u.course, r.confidence, r.timestamp
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
        """
        params = []
        if selected_date:
            query += " WHERE DATE(r.timestamp) = ?"
            params.append(selected_date)
        query += " ORDER BY r.timestamp DESC"

        c.execute(query, params)
        logs = c.fetchall()
        conn.close()
 
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Name', 'SR Code', 'Course', 'Confidence (%)', 'Timestamp'])
        for name, sr_code, course, confidence, timestamp in logs:
            confidence = _coerce_confidence(confidence)
            conf_value = f"{confidence * 100:.1f}" if isinstance(confidence, (int, float)) else ""
            writer.writerow([name, sr_code, course, conf_value, timestamp])
 
        response = make_response(output.getvalue())
        filename = f"library_logs_{date_for_name.strftime('%m-%d-%Y')}.csv"
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-type'] = 'text/csv'
        log_action("EXPORT_LOGS")
        return response
 

    return bp
