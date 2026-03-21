import os
import shutil
import sqlite3
import struct
import csv
import io
import math
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, flash, redirect, request, session, url_for, current_app, jsonify, send_from_directory

from auth import (
    create_staff,
    get_all_staff,
    log_action,
    login_required,
    role_required,
    toggle_staff_status,
)
from routes.ml_analytics import run_ml_analytics


def init_imported_logs_table(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS imported_logs (
            import_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sr_code TEXT NOT NULL,
            name TEXT,
            gender TEXT,
            program TEXT,
            year_level TEXT,
            timestamp TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            import_batch TEXT
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_srcode
        ON imported_logs(sr_code)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_timestamp
        ON imported_logs(timestamp)
        """
    )
    conn.commit()
    conn.close()


def create_routes_blueprint(deps):
    bp = Blueprint("routes", __name__)
    init_imported_logs_table(deps["db_path"])

    def _spa_index():
        built_path = os.path.join(current_app.static_folder, "react", "index.html")
        if os.path.exists(built_path):
            return send_from_directory(os.path.join(current_app.static_folder, "react"), "index.html")
        # Dev fallback: serve Vite index.html directly if build output is missing.
        return send_from_directory(os.path.join("frontend"), "index.html")

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

    def _ensure_settings_table(db_path):
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def _get_setting(db_path, key, default=None):
        _ensure_settings_table(db_path)
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default

    def _set_setting(db_path, key, value):
        _ensure_settings_table(db_path)
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()
        conn.close()

    def _dashboard_data():
        from datetime import date, timedelta

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()

        # ── Existing queries (kept exactly as before) ──────────────

        c.execute("SELECT COUNT(*) FROM recognition_log")
        total_logs = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(*) FROM recognition_log
            WHERE DATE(timestamp) = DATE('now')
        """)
        today_logs = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(DISTINCT user_id)
            FROM recognition_log
            WHERE DATE(timestamp) = DATE('now')
        """)
        today_unique = c.fetchone()[0]

        c.execute("SELECT confidence FROM recognition_log")
        conf_values = []
        for (confidence,) in c.fetchall():
            value = _coerce_confidence(confidence)
            if value is not None:
                conf_values.append(value)
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else None

        max_occupancy_setting = _get_setting(deps["db_path"], "max_occupancy", "300")
        try:
            max_occupancy = int(max_occupancy_setting)
        except (TypeError, ValueError):
            max_occupancy = 300
        current_occupancy = min(today_unique or 0, max_occupancy)
        occupancy_remaining = max(max_occupancy - current_occupancy, 0)
        occupancy_ratio = (current_occupancy / max_occupancy) if max_occupancy else 0
        if occupancy_ratio >= 0.9:
            occupancy_status = "Approaching capacity"
        elif occupancy_ratio >= 0.7:
            occupancy_status = "Moderately busy"
        else:
            occupancy_status = "Available"

        avg_confidence = round((avg_conf or 0) * 100, 1)

        # ── Total registered students ──────────────────────────────

        c.execute("SELECT COUNT(*) FROM users")
        total_students = c.fetchone()[0]

        # ── Daily visitors — last 14 days ──────────────────────────

        c.execute("""
            SELECT DATE(timestamp) as day, COUNT(*) as count
            FROM recognition_log
            WHERE DATE(timestamp) >= DATE('now', '-13 days')
            GROUP BY day
            ORDER BY day ASC
        """)
        date_map = {row[0]: row[1] for row in c.fetchall()}
        daily_visitors = [
            {
                "date": (date.today() - timedelta(days=i)).isoformat()[5:],
                "count": date_map.get(
                    (date.today() - timedelta(days=i)).isoformat(), 0
                )
            }
            for i in range(13, -1, -1)
        ]

        # ── Course distribution ────────────────────────────────────

        c.execute("""
            SELECT u.course, COUNT(DISTINCT r.user_id) as count
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            WHERE u.course IS NOT NULL AND u.course != ''
            GROUP BY u.course
            ORDER BY count DESC
            LIMIT 8
        """)
        course_distribution = [
            {"course": row[0], "count": row[1]}
            for row in c.fetchall()
        ]

        # ── Peak hours (24-slot array, index = hour) ───────────────

        c.execute("""
            SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                   COUNT(*) as count
            FROM recognition_log
            GROUP BY hour
        """)
        hour_map = {row[0]: row[1] for row in c.fetchall()}
        peak_hours = [hour_map.get(h, 0) for h in range(24)]

        # ── Top 10 frequent visitors ───────────────────────────────

        c.execute("""
            SELECT u.name, u.sr_code, COUNT(r.log_id) as visits
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            GROUP BY r.user_id
            ORDER BY visits DESC
            LIMIT 10
        """)
        top_visitors = [
            {"name": row[0], "sr_code": row[1], "visits": row[2]}
            for row in c.fetchall()
        ]
        
        # ── Weekly Heatmap (Day 0=Mon to 6=Sun, Hours 7AM–7PM) ──
        # SQLite: strftime('%w') returns 0=Sun,1=Mon,...6=Sat
        # We remap to Mon=0 ... Sun=6
        c.execute("""
            SELECT
                CASE CAST(strftime('%w', timestamp) AS INTEGER)
                    WHEN 0 THEN 6
                    ELSE CAST(strftime('%w', timestamp) AS INTEGER) - 1
                END as day_of_week,
                CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                COUNT(*) as count
            FROM recognition_log
            WHERE CAST(strftime('%H', timestamp) AS INTEGER) BETWEEN 7 AND 19
            GROUP BY day_of_week, hour
            ORDER BY day_of_week, hour
        """)
        heatmap_raw = c.fetchall()
 
        # Build 7x13 grid (7 days x 13 hours from 7AM to 7PM)
        heatmap_map = {}
        for day, hour, count in heatmap_raw:
            heatmap_map[(day, hour)] = count
 
        weekly_heatmap = [
            {
                "day": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d],
                "values": [heatmap_map.get((d, h), 0) for h in range(7, 20)]
            }
            for d in range(7)
        ]
 
        # ── Monthly Visitors — last 6 months ──────────────────────
        c.execute("""
            SELECT
                strftime('%Y-%m', timestamp) as month,
                COUNT(*) as count
            FROM recognition_log
            WHERE DATE(timestamp) >= DATE('now', 'start of month', '-5 months')
            GROUP BY month
            ORDER BY month ASC
        """)
        monthly_raw = {row[0]: row[1] for row in c.fetchall()}

        import calendar

        def _shift_month(year, month, delta):
            total = (year * 12 + (month - 1)) + delta
            return total // 12, (total % 12) + 1

        current_year = date.today().year
        current_month = date.today().month
        monthly_visitors = []
        for delta in range(-5, 1):
            year, month_num = _shift_month(current_year, current_month, delta)
            month_key = f"{year:04d}-{month_num:02d}"
            monthly_visitors.append(
                {
                    "month": f"{calendar.month_abbr[month_num]} {year}",
                    "count": monthly_raw.get(month_key, 0),
                }
            )

        conn.close()

        return {
            # ── Original fields (kept for backward compatibility) ──
            "total_logs": total_logs,
            "today_logs": today_logs,
            "avg_confidence": avg_confidence,
            "current_occupancy": current_occupancy,
            "max_occupancy": max_occupancy,
            "occupancy_remaining": occupancy_remaining,
            "occupancy_status": occupancy_status,
            # ── New fields for enhanced dashboard ──────────────────
            "total_students": total_students,
            "daily_visitors": daily_visitors,
            "course_distribution": course_distribution,
            "peak_hours": peak_hours,
            "top_visitors": top_visitors,
            "weekly_heatmap": weekly_heatmap,
            "monthly_visitors": monthly_visitors,
        }

    @bp.route("/policy", endpoint="policy_page")
    @login_required
    @role_required("super_admin", "library_admin")
    def policy_page():
        return _spa_index()

    @bp.route("/dashboard", endpoint="dashboard_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def dashboard_page():
        return _spa_index()

    @bp.route("/routes", endpoint="route_list_page")
    @login_required
    @role_required("super_admin", "library_admin")
    def route_list_page():
        return _spa_index()

    @bp.route("/manage-users", endpoint="manage_users")
    @login_required
    @role_required("super_admin")
    def manage_users():
        return _spa_index()

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
            return redirect(url_for("routes.manage_users"))

        if role not in allowed_roles:
            flash("Invalid role. Only Admin or Staff can be created here.", "error")
            return redirect(url_for("routes.manage_users"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("routes.manage_users"))

        success, message = create_staff(username, password, full_name, role)
        if success:
            log_action("CREATE_STAFF", target=username)
            flash(f"User '{username}' created successfully.", "success")
        else:
            flash(message, "error")

        return redirect(url_for("routes.manage_users"))

    @bp.route("/manage-users/toggle/<int:staff_id>", methods=["POST"], endpoint="manage_users_toggle")
    @login_required
    @role_required("super_admin")
    def manage_users_toggle(staff_id):
        if staff_id == session.get("staff_id"):
            flash("You cannot deactivate your own account.", "error")
            return redirect(url_for("routes.manage_users"))

        toggle_staff_status(staff_id)
        log_action("TOGGLE_STAFF_STATUS", target=str(staff_id))
        flash("User status updated.", "success")
        return redirect(url_for("routes.manage_users"))

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
            max_occupancy_raw = request.form.get("max_occupancy", "").strip()
            if max_occupancy_raw:
                try:
                    max_occupancy_value = max(1, int(max_occupancy_raw))
                except ValueError:
                    max_occupancy_value = 300
                _set_setting(deps["db_path"], "max_occupancy", max_occupancy_value)
            return redirect(url_for("routes.settings"))

        return _spa_index()

    @bp.route("/api/stats", endpoint="get_stats")
    @login_required
    @role_required("super_admin", "library_admin")
    def get_stats():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT u.user_id, u.name, r.confidence
            FROM users u
            LEFT JOIN recognition_log r ON u.user_id = r.user_id
            """
        )
        stats = {}
        for user_id, name, confidence in c.fetchall():
            entry = stats.setdefault(user_id, {"name": name, "count": 0, "sum": 0.0, "n": 0})
            if confidence is not None:
                entry["count"] += 1
                value = _coerce_confidence(confidence)
                if value is not None:
                    entry["sum"] += value
                    entry["n"] += 1
        conn.close()

        return {
            "user_count": deps["get_user_count"](),
            "recognition_stats": [
                {
                    "name": entry["name"],
                    "recognitions": entry["count"],
                    "avg_confidence": (entry["sum"] / entry["n"]) if entry["n"] else None,
                }
                for entry in sorted(stats.values(), key=lambda item: item["count"], reverse=True)
            ],
        }

    @bp.route("/registered-profiles", endpoint="registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles():
        return _spa_index()

    @bp.route("/archive-profiles", endpoint="registered_profiles_archive")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles_archive():
        return _spa_index()

    @bp.route("/archive-profiles/submit", methods=["POST"], endpoint="registered_profiles_archive_submit")
    @login_required
    @role_required("super_admin", "library_admin")
    def registered_profiles_archive_submit():
        user_ids = request.form.getlist("user_ids")
        if not user_ids:
            flash("No profiles selected for archiving.", "error")
            return redirect(url_for("routes.registered_profiles_archive"))

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            f"""
            UPDATE users
            SET archived_at = CURRENT_TIMESTAMP
            WHERE user_id IN ({",".join("?" * len(user_ids))})
            """,
            user_ids,
        )
        conn.commit()
        conn.close()

        log_action("ARCHIVE_PROFILES", target=",".join(user_ids))
        flash("Selected profiles archived.", "success")
        return redirect(url_for("routes.archived_profiles"))

    @bp.route("/archived-profiles", endpoint="archived_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def archived_profiles():
        return _spa_index()

    @bp.route("/archived-profiles/restore", methods=["POST"], endpoint="archived_profiles_restore")
    @login_required
    @role_required("super_admin", "library_admin")
    def archived_profiles_restore():
        user_ids = request.form.getlist("user_ids")
        if not user_ids:
            flash("No profiles selected for restore.", "error")
            return redirect(url_for("routes.archived_profiles"))

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            f"""
            UPDATE users
            SET archived_at = NULL, last_updated = CURRENT_TIMESTAMP
            WHERE user_id IN ({",".join("?" * len(user_ids))})
            """,
            user_ids,
        )
        conn.commit()
        conn.close()

        log_action("RESTORE_PROFILES", target=",".join(user_ids))
        flash("Selected profiles restored.", "success")
        return redirect(url_for("routes.registered_profiles"))

    @bp.route("/analytics-reports", endpoint="analytics_reports")
    @login_required
    @role_required("super_admin", "library_admin")
    def analytics_reports():
        range_key = request.args.get("range", "14d").strip().lower()
        range_map = {
            "today": 1,
            "7d": 7,
            "14d": 14,
            "30d": 30,
        }
        range_days = range_map.get(range_key, 14)

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

        c.execute(
            """
            SELECT COUNT(DISTINCT user_id)
            FROM recognition_log
            WHERE DATE(timestamp) = DATE('now')
            """
        )
        today_unique = c.fetchone()[0]

        c.execute("SELECT confidence FROM recognition_log")
        conf_values = []
        for (confidence,) in c.fetchall():
            value = _coerce_confidence(confidence)
            if value is not None:
                conf_values.append(value)
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else None

        max_occupancy_setting = _get_setting(deps["db_path"], "max_occupancy", "300")
        try:
            max_occupancy = int(max_occupancy_setting)
        except (TypeError, ValueError):
            max_occupancy = 300
        current_occupancy = min(today_unique or 0, max_occupancy)
        occupancy_remaining = max(max_occupancy - current_occupancy, 0)
        occupancy_ratio = (current_occupancy / max_occupancy) if max_occupancy else 0
        if occupancy_ratio >= 0.9:
            occupancy_status = "Approaching capacity"
        elif occupancy_ratio >= 0.7:
            occupancy_status = "Moderately busy"
        else:
            occupancy_status = "Available"

        start_date = date.today() - timedelta(days=range_days - 1)
        c.execute(
            """
            SELECT DATE(timestamp) as day,
                   confidence
            FROM recognition_log
            WHERE DATE(timestamp) >= ?
            """,
            (start_date.isoformat(),),
        )
        daily_counts_map = {}
        daily_conf_sum = {}
        daily_conf_n = {}
        for day, confidence in c.fetchall():
            daily_counts_map[day] = daily_counts_map.get(day, 0) + 1
            value = _coerce_confidence(confidence)
            if value is not None:
                daily_conf_sum[day] = daily_conf_sum.get(day, 0.0) + value
                daily_conf_n[day] = daily_conf_n.get(day, 0) + 1

        chart_labels = []
        daily_counts = []
        daily_avg_conf = []
        for i in range(range_days):
            day = (start_date + timedelta(days=i)).isoformat()
            chart_labels.append(day)
            cnt = daily_counts_map.get(day, 0)
            avg = None
            if daily_conf_n.get(day):
                avg = daily_conf_sum[day] / daily_conf_n[day]
            daily_counts.append(cnt)
            daily_avg_conf.append(round(avg * 100, 1) if avg is not None else None)

        c.execute(
            """
            SELECT u.user_id, u.name, u.sr_code, r.confidence
            FROM users u
            LEFT JOIN recognition_log r ON u.user_id = r.user_id
            """
        )
        top_map = {}
        for user_id, name, sr_code, confidence in c.fetchall():
            entry = top_map.setdefault(
                user_id, {"name": name, "sr_code": sr_code, "count": 0, "sum": 0.0, "n": 0}
            )
            if confidence is not None:
                entry["count"] += 1
                value = _coerce_confidence(confidence)
                if value is not None:
                    entry["sum"] += value
                    entry["n"] += 1
        top_users = sorted(top_map.values(), key=lambda item: item["count"], reverse=True)[:10]
        conn.close()

        avg_confidence = round((avg_conf or 0) * 100, 1)
        return _spa_index()

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
                return redirect(url_for("routes.registered_profiles"))
 
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
 
        return redirect(url_for("routes.registered_profiles"))
 
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

        return _spa_index()
 
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
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-type'] = 'text/csv'
        log_action("EXPORT_LOGS")
        return response

    @bp.route("/api/dashboard", methods=["GET"], endpoint="api_dashboard")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_dashboard():
        return jsonify(_dashboard_data())

    @bp.route("/api/settings", methods=["GET", "POST"], endpoint="api_settings")
    @login_required
    @role_required("super_admin")
    def api_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            deps["set_thresholds"](
                float(payload.get("threshold", deps["get_thresholds"]()[0])),
                bool(payload.get("adaptive_threshold")),
                float(payload.get("quality_threshold", deps["get_thresholds"]()[2])),
            )
            max_occupancy_raw = str(payload.get("max_occupancy", "")).strip()
            if max_occupancy_raw:
                try:
                    max_occupancy_value = max(1, int(max_occupancy_raw))
                except ValueError:
                    max_occupancy_value = 300
                _set_setting(deps["db_path"], "max_occupancy", max_occupancy_value)

        threshold, adaptive_threshold, quality_threshold = deps["get_thresholds"]()
        max_occupancy_setting = _get_setting(deps["db_path"], "max_occupancy", "300")
        try:
            max_occupancy = int(max_occupancy_setting)
        except (TypeError, ValueError):
            max_occupancy = 300
        return jsonify(
            {
                "user_count": deps["get_user_count"](),
                "threshold": threshold,
                "adaptive_threshold": adaptive_threshold,
                "quality_threshold": quality_threshold,
                "max_occupancy": max_occupancy,
            }
        )

    @bp.route("/api/registered-profiles", methods=["GET"], endpoint="api_registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_registered_profiles():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, course, created_at, last_updated
            FROM users
            ORDER BY created_at DESC
            """
        )
        rows = [
            {
                "user_id": row[0],
                "name": row[1] or "-",
                "sr_code": row[2] or "-",
                "course": row[3] or "-",
                "created_at": row[4] or "-",
                "last_updated": row[5] or "-",
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/archive-profiles", methods=["GET"], endpoint="api_archive_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archive_profiles():
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
        rows = [
            {
                "user_id": row[0],
                "name": row[1] or "-",
                "sr_code": row[2] or "-",
                "course": row[3] or "-",
                "created_at": row[4] or "-",
                "last_updated": row[5] or "-",
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/archive-profiles/submit", methods=["POST"], endpoint="api_archive_profiles_submit")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archive_profiles_submit():
        payload = request.get_json(silent=True) or {}
        user_ids = payload.get("user_ids") or []
        if not user_ids:
            return jsonify({"success": False, "message": "No profiles selected for archiving."}), 400

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            f"""
            UPDATE users
            SET archived_at = CURRENT_TIMESTAMP
            WHERE user_id IN ({",".join("?" * len(user_ids))})
            """,
            user_ids,
        )
        conn.commit()
        conn.close()

        log_action("ARCHIVE_PROFILES", target=",".join(map(str, user_ids)))
        return jsonify({"success": True})

    @bp.route("/api/archived-profiles", methods=["GET"], endpoint="api_archived_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archived_profiles():
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
        rows = [
            {
                "user_id": row[0],
                "name": row[1] or "-",
                "sr_code": row[2] or "-",
                "course": row[3] or "-",
                "created_at": row[4] or "-",
                "last_updated": row[5] or "-",
                "archived_at": row[6] or "-",
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/archived-profiles/restore", methods=["POST"], endpoint="api_archived_profiles_restore")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archived_profiles_restore():
        payload = request.get_json(silent=True) or {}
        user_ids = payload.get("user_ids") or []
        if not user_ids:
            return jsonify({"success": False, "message": "No profiles selected for restore."}), 400

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            f"""
            UPDATE users
            SET archived_at = NULL, last_updated = CURRENT_TIMESTAMP
            WHERE user_id IN ({",".join("?" * len(user_ids))})
            """,
            user_ids,
        )
        conn.commit()
        conn.close()

        log_action("RESTORE_PROFILES", target=",".join(map(str, user_ids)))
        return jsonify({"success": True})

    @bp.route("/api/import-logs", methods=["POST"], endpoint="api_import_logs")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_import_logs():
        if "file" not in request.files:
            return jsonify({"success": False, "message": "No file uploaded."}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.lower().endswith(".csv"):
            return jsonify({"success": False, "message": "Only CSV files are accepted."}), 400

        try:
            content = file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            raw_rows = list(reader)
        except Exception as exc:
            return jsonify({"success": False, "message": f"Failed to read CSV: {exc}"}), 400

        if not raw_rows:
            return jsonify({"success": False, "message": "CSV file is empty."}), 400

        field_map = {
            "sr_code": ["sr_code", "srcode", "sr code", "student_id", "id"],
            "name": ["name", "full_name", "fullname", "student_name"],
            "gender": ["gender", "sex"],
            "program": ["program", "course", "department"],
            "year_level": ["year_level", "year", "yearlevel", "level"],
            "timestamp": ["timestamp", "date", "datetime", "visit_date", "date_time"],
        }

        def find_col(row_keys, candidates):
            lowered = {str(key).lower().strip(): key for key in row_keys if key}
            for candidate in candidates:
                if candidate in lowered:
                    return lowered[candidate]
            return None

        sample_keys = list(raw_rows[0].keys())
        col_map = {field: find_col(sample_keys, candidates) for field, candidates in field_map.items()}

        if not col_map["sr_code"]:
            return jsonify({"success": False, "message": "CSV must have an sr_code column."}), 400
        if not col_map["timestamp"]:
            return jsonify({"success": False, "message": "CSV must have a timestamp or date column."}), 400

        from datetime import datetime

        batch_id = datetime.now().strftime("batch_%Y%m%d_%H%M%S")
        batch_rows = []
        inserted = 0
        skipped = 0
        errors = []

        allowed_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
        ]

        for index, row in enumerate(raw_rows, start=2):
            sr_code = (row.get(col_map["sr_code"]) or "").strip()
            if not sr_code:
                skipped += 1
                continue

            raw_timestamp = (row.get(col_map["timestamp"]) or "").strip()
            parsed_timestamp = None
            for fmt in allowed_formats:
                try:
                    parsed_timestamp = datetime.strptime(raw_timestamp, fmt).strftime("%Y-%m-%d %H:%M:%S")
                    break
                except ValueError:
                    continue

            if not parsed_timestamp:
                errors.append(f"Row {index}: invalid timestamp '{raw_timestamp}'")
                skipped += 1
                continue

            name = (row.get(col_map["name"]) or "").strip() if col_map["name"] else ""
            gender = (row.get(col_map["gender"]) or "").strip() if col_map["gender"] else ""
            program = (row.get(col_map["program"]) or "").strip() if col_map["program"] else ""
            year_level = (row.get(col_map["year_level"]) or "").strip() if col_map["year_level"] else ""

            batch_rows.append((sr_code, name, gender, program, year_level, parsed_timestamp, batch_id))
            inserted += 1

        if not batch_rows:
            return jsonify(
                {
                    "success": False,
                    "message": "No valid rows found to import.",
                    "errors": errors[:10],
                }
            ), 400

        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.executemany(
            """
            INSERT INTO imported_logs
                (sr_code, name, gender, program, year_level, timestamp, import_batch)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            batch_rows,
        )
        conn.commit()

        c.execute("SELECT COUNT(*) FROM imported_logs")
        total_imported = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT import_batch) FROM imported_logs")
        total_batches = c.fetchone()[0]
        conn.close()

        log_action("IMPORT_LOGS", target=f"{inserted} rows, batch={batch_id}")

        return jsonify(
            {
                "success": True,
                "inserted": inserted,
                "skipped": skipped,
                "batch_id": batch_id,
                "total_imported": total_imported,
                "total_batches": total_batches,
                "errors": errors[:10],
                "message": f"Successfully imported {inserted} records.",
            }
        )

    @bp.route("/api/import-logs/summary", methods=["GET"], endpoint="api_import_logs_summary")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_import_logs_summary():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM imported_logs")
        total_imported = c.fetchone()[0]

        c.execute(
            """
            SELECT import_batch,
                   COUNT(*) AS count,
                   MIN(timestamp) AS earliest,
                   MAX(timestamp) AS latest,
                   MAX(imported_at) AS imported_at
            FROM imported_logs
            GROUP BY import_batch
            ORDER BY imported_at DESC, import_batch DESC
            """
        )
        batches = [
            {
                "batch_id": row[0],
                "count": row[1],
                "earliest": (row[2] or "")[:10],
                "latest": (row[3] or "")[:10],
                "imported_at": (row[4] or "")[:16],
            }
            for row in c.fetchall()
        ]

        c.execute("SELECT COUNT(*) FROM recognition_log")
        live_logs = c.fetchone()[0]
        conn.close()

        return jsonify(
            {
                "total_imported": total_imported,
                "live_logs": live_logs,
                "batches": batches,
            }
        )

    @bp.route("/api/import-logs/delete/<batch_id>", methods=["POST"], endpoint="api_import_logs_delete")
    @login_required
    @role_required("super_admin")
    def api_import_logs_delete(batch_id):
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute("DELETE FROM imported_logs WHERE import_batch = ?", (batch_id,))
        deleted = c.rowcount
        conn.commit()
        conn.close()

        log_action("DELETE_IMPORT_BATCH", target=batch_id)
        return jsonify({"success": True, "deleted": deleted})

    @bp.route("/api/analytics-reports", methods=["GET"], endpoint="api_analytics_reports")

    @login_required
    @role_required("super_admin", "library_admin")
    def api_analytics_reports():
        try:
            result = run_ml_analytics(deps["db_path"])
            status = 400 if result.get("error") else 200
            return jsonify(result), status
        except Exception as e:
            return jsonify({
                "message": "Analytics pipeline failed to run.",
                "details": str(e),
            }), 500

    @bp.route("/api/entry-exit-logs", methods=["GET"], endpoint="api_entry_exit_logs")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_entry_exit_logs():
        conn = sqlite3.connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT u.name, u.sr_code, r.confidence, r.timestamp
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            ORDER BY r.timestamp DESC
            LIMIT 500
            """
        )
        raw_logs = c.fetchall()
        rows = []
        for name, sr_code, confidence, timestamp in raw_logs:
            value = _coerce_confidence(confidence) or 0
            conf_pct = int(value * 100)
            date_value = timestamp[:10] if timestamp else ""
            rows.append(
                {
                    "name": name or "-",
                    "sr_code": sr_code or "-",
                    "conf_pct": conf_pct,
                    "timestamp": timestamp or "",
                    "date": date_value,
                }
            )
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/manage-users", methods=["GET"], endpoint="api_manage_users")
    @login_required
    @role_required("super_admin")
    def api_manage_users():
        staff_rows = get_all_staff()
        rows = [
            {
                "staff_id": row[0],
                "username": row[1],
                "full_name": row[2],
                "role": row[3],
                "is_active": bool(row[4]),
            }
            for row in staff_rows
        ]
        return jsonify({"rows": rows})

    @bp.route("/api/manage-users/create", methods=["POST"], endpoint="api_manage_users_create")
    @login_required
    @role_required("super_admin")
    def api_manage_users_create():
        payload = request.get_json(silent=True) or {}
        full_name = (payload.get("full_name") or "").strip()
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        role = (payload.get("role") or "").strip()

        allowed_roles = {"library_admin", "library_staff"}
        if not full_name or not username or not password:
            return jsonify({"success": False, "message": "Full name, username, and password are required."}), 400

        if role not in allowed_roles:
            return jsonify({"success": False, "message": "Invalid role. Only Admin or Staff can be created here."}), 400

        if len(password) < 8:
            return jsonify({"success": False, "message": "Password must be at least 8 characters."}), 400

        success, message = create_staff(username, password, full_name, role)
        if success:
            log_action("CREATE_STAFF", target=username)
            return jsonify({"success": True})
        return jsonify({"success": False, "message": message}), 400

    @bp.route("/api/manage-users/toggle/<int:staff_id>", methods=["POST"], endpoint="api_manage_users_toggle")
    @login_required
    @role_required("super_admin")
    def api_manage_users_toggle(staff_id):
        if staff_id == session.get("staff_id"):
            return jsonify({"success": False, "message": "You cannot deactivate your own account."}), 400

        toggle_staff_status(staff_id)
        log_action("TOGGLE_STAFF_STATUS", target=str(staff_id))
        return jsonify({"success": True})

    @bp.route("/api/route-list", methods=["GET"], endpoint="api_route_list")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_route_list():
        routes_list = []
        for i, rule in enumerate(sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule), start=1):
            routes_list.append(
                {
                    "i": i,
                    "uri": rule.rule,
                    "name": rule.endpoint,
                    "action": rule.endpoint,
                    "methods": sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}),
                }
            )
        return jsonify({"routes": routes_list})

    @bp.route("/api/policy", methods=["GET"], endpoint="api_policy")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_policy():
        policy_html = deps["render_markdown_as_html"](Path("static/content/markdown/policy.md"))
        return jsonify({"policy": policy_html})

    return bp
