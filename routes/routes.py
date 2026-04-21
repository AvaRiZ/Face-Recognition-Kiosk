import os
import re
import shutil
import struct
import csv
import io
import math
import time
import base64
import calendar
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
from flask import Blueprint, flash, redirect, request, session, url_for, current_app, jsonify, send_from_directory

from auth import (
    create_staff,
    get_all_staff,
    log_action,
    login_required,
    role_required,
    toggle_staff_status,
)
from core.models import User
from core.program_catalog import (
    build_program_lookup,
    is_program_code,
    normalize_program_name,
    resolve_program_name,
)
from db import connect as db_connect
from routes.ml_analytics import run_ml_analytics
from app.realtime import emit_analytics_update
from services.embedding_service import count_embeddings, merge_embeddings_by_model, normalize_embeddings_by_model
from services.versioning_service import bump_profiles_version, bump_settings_version, ensure_version_settings
from utils.image_utils import crop_face_region


def init_imported_logs_table(db_path):
    conn = db_connect(db_path)
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
    ensure_version_settings(deps["db_path"])

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

    def _decode_uploaded_image(file_storage):
        if not file_storage:
            return None
        raw = file_storage.read()
        if not raw:
            return None
        file_storage.stream.seek(0)
        buffer = np.frombuffer(raw, dtype=np.uint8)
        if buffer.size == 0:
            return None
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)

    def _registration_sample_previews(reg_state):
        samples = reg_state.pending_registration or reg_state.captured_samples or []
        previews = []
        preview_limit = reg_state.total_retained_samples if reg_state.pending_registration else reg_state.max_captures
        for index, sample in enumerate(samples[:preview_limit]):
            face_crop = getattr(sample, "face_crop", None)
            if face_crop is None or getattr(face_crop, "size", 0) == 0:
                continue
            preview = face_crop
            if preview.shape[0] > 160:
                scale = 160.0 / preview.shape[0]
                preview = cv2.resize(
                    preview,
                    (max(1, int(preview.shape[1] * scale)), 160),
                    interpolation=cv2.INTER_AREA,
                )
            success, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not success:
                continue
            previews.append(
                {
                    "id": index,
                    "pose": getattr(sample, "pose", None),
                    "quality_score": round(float(getattr(sample, "quality", 0.0)), 2),
                    "image_url": "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii"),
                }
            )
        return previews

    def _registration_status_payload(reg_state):
        progress = deps["get_registration_progress"]()
        total_progress = progress.get("total_progress", {})
        captured_total = int(total_progress.get("captured", reg_state.capture_count))
        stream_status = deps["stream_status"]() if deps.get("stream_status") else {"state": "unknown", "message": "Camera status unavailable."}
        return {
            "capture_count": captured_total,
            "max_captures": reg_state.max_captures,
            "has_pending_registration": bool(reg_state.pending_registration),
            "is_in_progress": reg_state.in_progress,
            "web_session_active": bool(reg_state.web_session_active),
            "session_expired": bool(getattr(reg_state, "session_expired", False)),
            "detection_paused": bool(deps["detection_paused"]()),
            "sample_previews": _registration_sample_previews(reg_state),
            "required_poses": progress["required_poses"],
            "current_pose": progress["current_pose"],
            "current_pose_index": progress["current_pose_index"],
            "pose_progress": progress["pose_progress"],
            "total_progress": progress["total_progress"],
            "ready_to_submit": progress["ready_to_submit"],
            "camera_stream": stream_status,
        }

    def _registration_error_payload(reg_state, message: str, **extra):
        payload = _registration_status_payload(reg_state)
        payload.update({"success": False, "message": message, **extra})
        return payload

    def _has_complete_pending_registration(reg_state) -> bool:
        pending_registration = reg_state.pending_registration or []
        if not deps["is_registration_ready"]():
            return False
        return len(pending_registration) >= int(reg_state.total_retained_samples)

    def _validate_registration_fields(name: str, sr_code: str, gender: str, program: str):
        allowed_genders = {"Male", "Female", "Other"}
        normalized_name = " ".join(name.split())
        normalized_program = " ".join(program.split())
        normalized_sr_code = sr_code.strip()

        if not normalized_name or not normalized_sr_code or not gender or not normalized_program:
            return False, "Name, SR Code, gender, and program are required.", None

        if "," not in normalized_name:
            return False, "Use the name format: Last Name, First Name.", "name"

        last_name, first_name = [part.strip() for part in normalized_name.split(",", 1)]
        if not last_name or not first_name:
            return False, "Use the name format: Last Name, First Name.", "name"

        if not re.fullmatch(r"[A-Za-z][A-Za-z .,'-]{1,79}", normalized_name):
            return False, "Name contains invalid characters.", "name"

        if not re.fullmatch(r"\d{2}-\d{5}", normalized_sr_code):
            return False, "SR Code must use the format 23-12345.", "sr_code"

        if gender not in allowed_genders:
            return False, "Please select a valid gender.", "gender"

        if len(normalized_program) < 4 or len(normalized_program) > 120:
            return False, "Program must be between 4 and 120 characters.", "program"

        if not re.fullmatch(r"[A-Za-z0-9&(),./' -]+", normalized_program):
            return False, "Program contains invalid characters.", "program"

        return True, "", None

    def _extract_largest_face(image):
        if image is None or image.size == 0:
            return None, None, None, 0

        yolo_model = deps.get("yolo_model")
        if yolo_model is not None:
            try:
                yolo_results = yolo_model.predict(
                    source=image,
                    verbose=False,
                    device=deps.get("yolo_device", "cpu"),
                )
                for result in yolo_results or []:
                    if result.boxes is None:
                        continue

                    best_box = None
                    best_index = -1
                    best_area = -1
                    for idx, box in enumerate(result.boxes):
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        w = x2 - x1
                        h = y2 - y1
                        if w < deps["config"].min_face_size or h < deps["config"].min_face_size:
                            continue
                        area = w * h
                        if area > best_area:
                            best_area = area
                            best_box = box
                            best_index = idx

                    if best_box is not None:
                        x1, y1, x2, y2 = map(int, best_box.xyxy[0])
                        face_crop, _clamped_bbox = crop_face_region(image, x1, y1, x2, y2)
                        if face_crop is not None:
                            landmarks = None
                            keypoints_xy = getattr(getattr(result, "keypoints", None), "xy", None)
                            if keypoints_xy is not None and best_index >= 0:
                                try:
                                    if hasattr(keypoints_xy, "detach"):
                                        keypoints_xy = keypoints_xy.detach().cpu().numpy()
                                    else:
                                        keypoints_xy = keypoints_xy.cpu().numpy() if hasattr(keypoints_xy, "cpu") else keypoints_xy
                                    if best_index < len(keypoints_xy):
                                        points = keypoints_xy[best_index]
                                        landmarks = {
                                            "left_eye": (float(points[0][0] - x1), float(points[0][1] - y1)) if len(points) > 0 else None,
                                            "right_eye": (float(points[1][0] - x1), float(points[1][1] - y1)) if len(points) > 1 else None,
                                            "nose": (float(points[2][0] - x1), float(points[2][1] - y1)) if len(points) > 2 else None,
                                            "mouth_left": (float(points[3][0] - x1), float(points[3][1] - y1)) if len(points) > 3 else None,
                                            "mouth_right": (float(points[4][0] - x1), float(points[4][1] - y1)) if len(points) > 4 else None,
                                        }
                                except Exception:
                                    landmarks = None

                            detection_confidence = (
                                float(best_box.conf[0]) if best_box.conf is not None else None
                            )
                            selected_area = int(face_crop.shape[0] * face_crop.shape[1])
                            return face_crop, detection_confidence, landmarks, selected_area
            except Exception:
                pass

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        classifier = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if classifier.empty():
            return None, None, None, 0

        faces = classifier.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(deps["config"].min_face_size, deps["config"].min_face_size),
        )
        if len(faces) == 0:
            return None, None, None, 0

        x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
        face_crop, _clamped_bbox = crop_face_region(image, x, y, x + w, y + h)
        if face_crop is None:
            return None, None, None, 0

        detection_confidence = min(1.0, max(0.35, float((w * h) / max(image.shape[0] * image.shape[1], 1))))
        selected_area = int(face_crop.shape[0] * face_crop.shape[1])
        return face_crop, detection_confidence, None, selected_area

    def _ensure_settings_table(db_path):
        conn = db_connect(db_path)
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
        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else default

    def _set_setting(db_path, key, value):
        _ensure_settings_table(db_path)
        conn = db_connect(db_path)
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

    def _monthly_program_visits_data(selected_year=None):
        import calendar

        current_year = date.today().year
        try:
            year = int(selected_year or current_year)
        except (TypeError, ValueError):
            year = current_year

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        c.execute(
            """
            SELECT program_name
            FROM programs
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY program_name ASC
            """
        )
        program_names = [row[0] for row in c.fetchall() if row[0]]

        c.execute(
            """
            SELECT DISTINCT SUBSTR(CAST(timestamp AS TEXT), 1, 4) AS year
            FROM recognition_log
            WHERE timestamp IS NOT NULL AND TRIM(CAST(timestamp AS TEXT)) != ''
            ORDER BY year DESC
            """
        )
        available_years = {current_year}
        for (raw_year,) in c.fetchall():
            try:
                available_years.add(int(raw_year))
            except (TypeError, ValueError):
                continue
        available_years = sorted(available_years, reverse=True)

        if year not in available_years:
            if available_years:
                year = available_years[0]
            else:
                available_years = [current_year]
                year = current_year

        c.execute(
            """
            SELECT
                COALESCE(NULLIF(TRIM(u.course), ''), 'Unassigned') AS program,
                SUBSTR(CAST(r.timestamp AS TEXT), 6, 2) AS month_num,
                COUNT(*) AS visit_count
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            WHERE SUBSTR(CAST(r.timestamp AS TEXT), 1, 4) = ?
            GROUP BY program, month_num
            ORDER BY program ASC, month_num ASC
            """,
            (str(year),),
        )
        raw_rows = c.fetchall()
        conn.close()

        grouped = {program_name: [0] * 12 for program_name in program_names}
        for program, month_num, visit_count in raw_rows:
            month_index = int(month_num or 0)
            if month_index < 1 or month_index > 12:
                continue
            grouped.setdefault(program, [0] * 12)[month_index - 1] = int(visit_count or 0)

        rows = []
        overall_monthly = [0] * 12
        for program in sorted(grouped):
            monthly_counts = grouped[program]
            overall_total = sum(monthly_counts)
            for idx, count in enumerate(monthly_counts):
                overall_monthly[idx] += count
            rows.append(
                {
                    "program": program,
                    "months": monthly_counts,
                    "overall_total": overall_total,
                }
            )

        overall_row = {
            "program": "Overall Total",
            "months": overall_monthly,
            "overall_total": sum(overall_monthly),
        }

        return {
            "year": year,
            "years": available_years,
            "months": [calendar.month_abbr[idx] for idx in range(1, 13)],
            "rows": rows,
            "overall_row": overall_row,
        }

    def _dashboard_filter_window(filter_key=None):
        filter_options = {
            "today": {"days": 1, "label": "Today"},
            "last_7_days": {"days": 7, "label": "Last 7 Days"},
            "last_14_days": {"days": 14, "label": "Last 14 Days"},
            "last_30_days": {"days": 30, "label": "Last 30 Days"},
            "last_90_days": {"days": 90, "label": "Last 90 Days"},
        }
        normalized_key = (filter_key or "last_14_days").strip().lower()
        if normalized_key not in filter_options:
            normalized_key = "last_14_days"

        option = filter_options[normalized_key]
        end_date = date.today()
        start_date = end_date - timedelta(days=option["days"] - 1)
        return {
            "key": normalized_key,
            "label": option["label"],
            "days": option["days"],
            "start_date": start_date,
            "end_date": end_date,
        }

    def _dashboard_data(filter_key=None):
        filter_window = _dashboard_filter_window(filter_key)
        start_date = filter_window["start_date"]
        end_date = filter_window["end_date"]
        range_params = (start_date.isoformat(), end_date.isoformat())

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        # ── Existing queries (kept exactly as before) ──────────────

        c.execute("""
            SELECT COUNT(*)
            FROM recognition_log
            WHERE DATE(timestamp) BETWEEN ? AND ?
        """, range_params)
        total_logs = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(*) FROM recognition_log
            WHERE DATE(timestamp) = DATE('now')
        """)
        today_logs = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(DISTINCT user_id)
            FROM recognition_log
            WHERE DATE(timestamp) BETWEEN ? AND ?
        """, range_params)
        unique_visitors = c.fetchone()[0]

        c.execute("""
            SELECT confidence
            FROM recognition_log
            WHERE DATE(timestamp) BETWEEN ? AND ?
        """, range_params)
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
        current_occupancy = min(unique_visitors or 0, max_occupancy)
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
            WHERE DATE(timestamp) BETWEEN ? AND ?
            GROUP BY day
            ORDER BY day ASC
        """, range_params)
        date_map = {row[0]: row[1] for row in c.fetchall()}
        daily_visitors = []
        for day_offset in range(filter_window["days"]):
            current_day = start_date + timedelta(days=day_offset)
            daily_visitors.append(
                {
                    "date": current_day.strftime("%m-%d"),
                    "count": date_map.get(current_day.isoformat(), 0),
                }
            )

        # ── Course distribution ────────────────────────────────────

        c.execute("""
            SELECT u.course, COUNT(DISTINCT r.user_id) as count
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            WHERE u.course IS NOT NULL
              AND u.course != ''
              AND DATE(r.timestamp) BETWEEN ? AND ?
            GROUP BY u.course
            ORDER BY count DESC
            LIMIT 8
        """, range_params)
        program_distribution = [
            {"program": row[0], "count": row[1]}
            for row in c.fetchall()
        ]

        # ── Peak hours (24-slot array, index = hour) ───────────────

        c.execute("""
            SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                   COUNT(*) as count
            FROM recognition_log
            WHERE DATE(timestamp) BETWEEN ? AND ?
            GROUP BY hour
        """, range_params)
        hour_map = {row[0]: row[1] for row in c.fetchall()}
        peak_hours = [hour_map.get(h, 0) for h in range(24)]

        # ── Top 10 frequent visitors ───────────────────────────────

        c.execute("""
            SELECT u.name, u.sr_code, COUNT(r.log_id) as visits
            FROM recognition_log r
            JOIN users u ON r.user_id = u.user_id
            GROUP BY u.user_id, u.name, u.sr_code
            ORDER BY visits DESC
            LIMIT 10
        """, range_params)
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
              AND DATE(timestamp) BETWEEN ? AND ?
            GROUP BY day_of_week, hour
            ORDER BY day_of_week, hour
        """, range_params)
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
            WHERE DATE(timestamp) BETWEEN ? AND ?
            GROUP BY month
            ORDER BY month ASC
        """, range_params)
        monthly_raw = {row[0]: row[1] for row in c.fetchall()}

        def _shift_month(year, month, delta):
            total = (year * 12 + (month - 1)) + delta
            return total // 12, (total % 12) + 1

        current_year = end_date.year
        current_month = end_date.month
        month_span = ((end_date.year - start_date.year) * 12) + (end_date.month - start_date.month)
        monthly_visitors = []
        for delta in range(-month_span, 1):
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
            "unique_visitors": unique_visitors,
            "filter_key": filter_window["key"],
            "filter_label": filter_window["label"],
            "filter_days": filter_window["days"],
            "filter_start_date": start_date.isoformat(),
            "filter_end_date": end_date.isoformat(),
            # ── New fields for enhanced dashboard ──────────────────
            "total_students": total_students,
            "daily_visitors": daily_visitors,
            "program_distribution": program_distribution,
            "peak_hours": peak_hours,
            "top_visitors": top_visitors,
            "weekly_heatmap": weekly_heatmap,
            "monthly_visitors": monthly_visitors,
        }

    @bp.route("/policy", endpoint="policy_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def policy_page():
        return _spa_index()

    @bp.route("/dashboard", endpoint="dashboard_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def dashboard_page():
        return _spa_index()

    @bp.route("/program-monthly-visits", endpoint="program_monthly_visits_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def program_monthly_visits_page():
        return _spa_index()

    @bp.route("/route-list")
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
                float(request.form.get("quality_threshold", deps["get_thresholds"]()[1])),
            )
            vector_index_top_k_raw = request.form.get("vector_index_top_k", "").strip()
            if vector_index_top_k_raw:
                try:
                    vector_index_top_k = max(1, int(vector_index_top_k_raw))
                except ValueError:
                    vector_index_top_k = 20
                deps["config"].vector_index_top_k = vector_index_top_k
                _set_setting(deps["db_path"], "vector_index_top_k", vector_index_top_k)

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
        conn = db_connect(deps["db_path"])
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

        conn = db_connect(deps["db_path"])
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

        conn = db_connect(deps["db_path"])
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
    @role_required("super_admin", "library_admin", "library_staff")
    def analytics_reports():
        range_key = request.args.get("range", "14d").strip().lower()
        range_map = {
            "today": 1,
            "7d": 7,
            "14d": 14,
            "30d": 30,
        }
        range_days = range_map.get(range_key, 14)

        conn = db_connect(deps["db_path"])
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
            conn = db_connect(deps["db_path"])
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
            conn = db_connect(deps["db_path"])
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

    @bp.route("/api/register-info", methods=["GET"], endpoint="api_register_info")
    def api_register_info():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        return jsonify(_registration_status_payload(reg_state))

    @bp.route("/api/detection/pause", methods=["POST"], endpoint="api_detection_pause")
    def api_detection_pause():
        deps["pause_detection"]()
        return jsonify({"success": True, "detection_paused": True})

    @bp.route("/api/detection/resume", methods=["POST"], endpoint="api_detection_resume")
    def api_detection_resume():
        deps["resume_detection"]()
        return jsonify({"success": True, "detection_paused": False})

    @bp.route("/api/register-reset", methods=["POST"], endpoint="api_register_reset")
    def api_register_reset():
        deps["reset_registration_state"]()
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        payload.update(
            {
                "success": True,
                "message": "Registration capture session reset.",
            }
        )
        return jsonify(payload)

    @bp.route("/api/register-session/start", methods=["POST"], endpoint="api_register_session_start")
    def api_register_session_start():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        if reg_state.in_progress and reg_state.pending_registration:
            payload = _registration_error_payload(
                reg_state,
                "A registration capture is already complete. Submit it or reset before starting a new session.",
            )
            return jsonify(payload), 409

        if reg_state.manual_active:
            payload = _registration_error_payload(
                reg_state,
                "A registration capture is already in progress.",
            )
            return jsonify(payload), 409

        if reg_state.web_session_active or reg_state.manual_requested:
            payload = _registration_error_payload(
                reg_state,
                "A registration session is already active and waiting for a student.",
            )
            return jsonify(payload), 409

        started = deps["start_web_registration_session"]()
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        if not started:
            payload.update(
                {
                    "success": False,
                    "message": "Unable to start a new registration session because another registration step is already active.",
                }
            )
            return jsonify(payload), 409

        payload.update(
            {
                "success": True,
                "message": "Registration session started. Keep the student in frame to capture required samples.",
            }
        )
        return jsonify(payload)

    @bp.route("/api/register-session/cancel", methods=["POST"], endpoint="api_register_session_cancel")
    def api_register_session_cancel():
        deps["expire_registration_session_if_needed"]()
        deps["cancel_web_registration_session"]()
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        payload.update(
            {
                "success": True,
                "message": "Registration session canceled.",
            }
        )
        return jsonify(payload)

    @bp.route("/register", methods=["POST"], endpoint="register_submit")
    def register_submit():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        pending_registration = reg_state.pending_registration or []
        if not pending_registration:
            return jsonify({"success": False, "message": "No pending registration samples found."}), 400
        if not _has_complete_pending_registration(reg_state):
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Registration capture is not complete yet. Finish all required face samples before saving.",
                    }
                ),
                400,
            )

        name = request.form.get("name", "").strip()
        sr_code = request.form.get("sr_code", "").strip()
        gender = request.form.get("gender", "").strip()
        program = request.form.get("program", "").strip()
        is_valid, validation_message, invalid_field = _validate_registration_fields(
            name,
            sr_code,
            gender,
            program,
        )
        if not is_valid:
            return jsonify({"success": False, "message": validation_message, "field": invalid_field}), 400

        repository = deps["repository"]
        existing = repository.get_user_by_sr_code(sr_code)
        if existing is not None:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": f"SR Code {sr_code} is already registered to {existing.name}. Use a different SR Code.",
                    }
                ),
                409,
            )

        all_embeddings = {}
        image_paths = []
        user_folder = os.path.join(deps["base_save_dir"], sr_code)
        os.makedirs(user_folder, exist_ok=True)

        for index, face_sample in enumerate(pending_registration):
            timestamp = int(time.time() * 1000)
            filename = os.path.join(user_folder, f"face_{timestamp}_{index}.jpg")
            if not cv2.imwrite(filename, face_sample.face_crop):
                return jsonify({"success": False, "message": "Failed to save captured face sample."}), 500
            image_paths.append(filename)
            all_embeddings = merge_embeddings_by_model(all_embeddings, face_sample.embeddings)

        user_id = repository.save_user(
            User(
                id=0,
                name=name,
                sr_code=sr_code,
                gender=gender,
                program=program,
                embeddings=all_embeddings,
                image_paths=image_paths,
                embedding_dim=0,
            )
        )
        bump_profiles_version(deps["db_path"])
        saved_user = repository.get_user_by_sr_code(sr_code)
        if saved_user:
            saved_user.id = user_id
            deps["replace_user"](saved_user)

        deps["complete_registration"]()
        total_embeddings = count_embeddings(normalize_embeddings_by_model(all_embeddings))
        redirect_url = url_for("routes.react_app", path="register")
        profile_payload = None
        if saved_user:
            profile_payload = {
                "user_id": saved_user.id,
                "name": saved_user.name,
                "sr_code": saved_user.sr_code,
                "gender": saved_user.gender,
                "program": saved_user.program,
            }
        return jsonify(
            {
                "success": True,
                "user_id": user_id,
                "updated": False,
                "embedding_count": total_embeddings,
                "message": (
                    f"Profile registered for {saved_user.name}."
                    if saved_user
                    else "Registration saved successfully."
                ),
                "profile": profile_payload,
                "redirect_url": redirect_url,
            }
        )
    
    @bp.route("/registered-profiles/delete/<int:user_id>", methods=["POST"], endpoint="delete_profile")
    @login_required
    @role_required("super_admin", "library_admin")
    def delete_profile(user_id):
        try:
            conn = db_connect(deps["db_path"])
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
            bump_profiles_version(deps["db_path"])
 
            # Remove from in-memory embeddings
            deps["remove_user_embedding"](user_id)
 
            log_action("DELETE_STUDENT", target=f"{name} ({sr_code})")
            flash(f"Student '{name}' deleted successfully.", "success")
 
        except Exception as e:
            flash(f"Failed to delete student: {str(e)}", "error")
 
        return redirect(url_for("routes.registered_profiles"))
 
    @bp.route("/entry-logs", endpoint="entry_logs")
    @bp.route("/entry-exit-logs", endpoint="entry_exit_logs")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def entry_logs():
        conn = db_connect(deps["db_path"])
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
 
    @bp.route("/entry-logs/export", endpoint="export_entry_logs")
    @bp.route("/entry-exit-logs/export", endpoint="export_logs")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def export_logs():
        import csv
        import io
        from datetime import date, datetime
        from flask import make_response

        conn = db_connect(deps["db_path"])
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
        writer.writerow(['Name', 'SR Code', 'Program', 'Confidence (%)', 'Timestamp'])
        for name, sr_code, program, confidence, timestamp in logs:
            confidence = _coerce_confidence(confidence)
            conf_value = f"{confidence * 100:.1f}" if isinstance(confidence, (int, float)) else ""
            writer.writerow([name, sr_code, program, conf_value, timestamp])
 
        response = make_response(output.getvalue())
        filename = f"library_entry_logs_{date_for_name.strftime('%m-%d-%Y')}.csv"
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-type'] = 'text/csv'
        log_action("EXPORT_LOGS")
        return response

    @bp.route("/program-monthly-visits/export", endpoint="export_program_monthly_visits")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def export_program_monthly_visits():
        from flask import make_response

        payload = _monthly_program_visits_data(request.args.get("year", "").strip())
        output = io.StringIO()
        writer = csv.writer(output)

        header = ["Program", *payload["months"], "Overall Total"]
        writer.writerow(header)

        for row in payload["rows"]:
            writer.writerow([row["program"], *row["months"], row["overall_total"]])

        overall_row = payload["overall_row"]
        writer.writerow([overall_row["program"], *overall_row["months"], overall_row["overall_total"]])

        response = make_response(output.getvalue())
        filename = f"program_monthly_visits_{payload['year']}.csv"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-type"] = "text/csv"
        log_action("EXPORT_PROGRAM_MONTHLY_VISITS", target=str(payload["year"]))
        return response

    @bp.route("/api/dashboard", methods=["GET"], endpoint="api_dashboard")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_dashboard():
        return jsonify(_dashboard_data(request.args.get("filter")))

    @bp.route("/api/settings/recognition", methods=["GET", "POST"], endpoint="api_settings")
    @bp.route("/api/settings", methods=["GET", "POST"])
    @login_required
    @role_required("super_admin")
    def api_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            try:
                threshold_value = float(payload.get("threshold", deps["get_thresholds"]()[0]))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "Invalid threshold value."}), 400
            try:
                quality_threshold_value = float(payload.get("quality_threshold", deps["get_thresholds"]()[1]))
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "Invalid quality threshold value."}), 400
            deps["set_thresholds"](threshold_value, quality_threshold_value)
            _set_setting(deps["db_path"], "threshold", threshold_value)
            _set_setting(deps["db_path"], "quality_threshold", quality_threshold_value)

            vector_index_top_k_raw = str(payload.get("vector_index_top_k", "")).strip()
            if vector_index_top_k_raw:
                try:
                    vector_index_top_k = max(1, int(vector_index_top_k_raw))
                except ValueError:
                    vector_index_top_k = 20
                deps["config"].vector_index_top_k = vector_index_top_k
                _set_setting(deps["db_path"], "vector_index_top_k", vector_index_top_k)

            max_occupancy_raw = str(payload.get("max_occupancy", "")).strip()
            if max_occupancy_raw:
                try:
                    max_occupancy_value = max(1, int(max_occupancy_raw))
                except ValueError:
                    max_occupancy_value = 300
                _set_setting(deps["db_path"], "max_occupancy", max_occupancy_value)
            bump_settings_version(deps["db_path"])

        threshold_setting = _get_setting(deps["db_path"], "threshold", str(deps["get_thresholds"]()[0]))
        quality_threshold_setting = _get_setting(
            deps["db_path"], "quality_threshold", str(deps["get_thresholds"]()[1])
        )
        try:
            threshold = float(threshold_setting)
        except (TypeError, ValueError):
            threshold = deps["get_thresholds"]()[0]
        try:
            quality_threshold = float(quality_threshold_setting)
        except (TypeError, ValueError):
            quality_threshold = deps["get_thresholds"]()[1]
        deps["set_thresholds"](threshold, quality_threshold)
        vector_index_top_k_setting = _get_setting(
            deps["db_path"],
            "vector_index_top_k",
            str(deps["config"].vector_index_top_k),
        )
        try:
            vector_index_top_k = max(1, int(vector_index_top_k_setting))
        except (TypeError, ValueError):
            vector_index_top_k = max(1, int(deps["config"].vector_index_top_k))
        deps["config"].vector_index_top_k = vector_index_top_k

        max_occupancy_setting = _get_setting(deps["db_path"], "max_occupancy", "300")
        try:
            max_occupancy = int(max_occupancy_setting)
        except (TypeError, ValueError):
            max_occupancy = 300
        return jsonify(
            {
                "user_count": deps["get_user_count"](),
                "threshold": threshold,
                "quality_threshold": quality_threshold,
                "vector_index_top_k": vector_index_top_k,
                "max_occupancy": max_occupancy,
            }
        )

    @bp.route("/api/registered-profiles", methods=["GET"], endpoint="api_registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_registered_profiles():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course AS program, created_at, last_updated
            FROM users
            ORDER BY created_at DESC
            """
        )
        rows = [
            {
                "user_id": row[0],
                "name": row[1] or "-",
                "sr_code": row[2] or "-",
                "gender": row[3] or "-",
                "program": row[4] or "-",
                "created_at": row[5] or "-",
                "last_updated": row[6] or "-",
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/profiles", methods=["POST"], endpoint="api_profiles_create")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_profiles_create():
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        sr_code = (payload.get("sr_code") or "").strip()
        gender = (payload.get("gender") or "").strip()
        program = (payload.get("program") or "").strip()

        is_valid, validation_message, invalid_field = _validate_registration_fields(name, sr_code, gender, program)
        if not is_valid:
            return jsonify({"success": False, "message": validation_message, "field": invalid_field}), 400

        repository = deps["repository"]
        existing = repository.get_user_by_sr_code(sr_code)
        if existing is not None:
            return jsonify({"success": False, "message": f"SR Code {sr_code} already exists."}), 409

        user_id = repository.save_user(
            User(
                id=0,
                name=name,
                sr_code=sr_code,
                gender=gender,
                program=program,
                embeddings={},
                image_paths=[],
                embedding_dim=0,
            )
        )
        saved_user = repository.get_user_by_sr_code(sr_code)
        if saved_user:
            deps["replace_user"](saved_user)
        bump_profiles_version(deps["db_path"])
        log_action("CREATE_PROFILE", target=f"{name} ({sr_code})")
        return jsonify({"success": True, "user_id": int(user_id)})

    @bp.route("/api/profiles/<int:user_id>", methods=["PUT"], endpoint="api_profiles_update")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_profiles_update(user_id):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        sr_code = (payload.get("sr_code") or "").strip()
        gender = (payload.get("gender") or "").strip()
        program = (payload.get("program") or "").strip()

        is_valid, validation_message, invalid_field = _validate_registration_fields(name, sr_code, gender, program)
        if not is_valid:
            return jsonify({"success": False, "message": validation_message, "field": invalid_field}), 400

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Profile not found."}), 404

        existing_for_sr_code = deps["repository"].get_user_by_sr_code(sr_code)
        if existing_for_sr_code is not None and int(existing_for_sr_code.id) != int(user_id):
            conn.close()
            return jsonify({"success": False, "message": f"SR Code {sr_code} already exists."}), 409

        c.execute(
            """
            UPDATE users
            SET name = ?, sr_code = ?, gender = ?, course = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (name, sr_code, gender, program, user_id),
        )
        try:
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "message": f"Failed to update profile: {exc}"}), 400
        conn.close()

        refreshed_user = deps["repository"].get_user_by_id(user_id)
        if refreshed_user:
            deps["replace_user"](refreshed_user)
        bump_profiles_version(deps["db_path"])
        log_action("UPDATE_PROFILE_RECORD", target=f"{name} ({sr_code})")
        return jsonify({"success": True, "user_id": int(user_id)})

    @bp.route("/api/archive-profiles", methods=["GET"], endpoint="api_archive_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archive_profiles():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course AS program, created_at, last_updated
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
                "gender": row[3] or "-",
                "program": row[4] or "-",
                "created_at": row[5] or "-",
                "last_updated": row[6] or "-",
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

        conn = db_connect(deps["db_path"])
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

        bump_profiles_version(deps["db_path"])
        log_action("ARCHIVE_PROFILES", target=",".join(map(str, user_ids)))
        return jsonify({"success": True})

    @bp.route("/api/archived-profiles", methods=["GET"], endpoint="api_archived_profiles")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_archived_profiles():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, name, sr_code, gender, course AS program, created_at, last_updated, archived_at
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
                "gender": row[3] or "-",
                "program": row[4] or "-",
                "created_at": row[5] or "-",
                "last_updated": row[6] or "-",
                "archived_at": row[7] or "-",
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

        conn = db_connect(deps["db_path"])
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

        bump_profiles_version(deps["db_path"])
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
        warnings_list = []
        program_resolution = {
            "resolved_from_registration": 0,
            "resolved_from_catalog": 0,
            "ambiguous_codes": 0,
            "unmatched_codes": 0,
        }

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT sr_code, NULLIF(TRIM(course), '')
            FROM users
            WHERE sr_code IS NOT NULL AND TRIM(sr_code) <> ''
            """
        )
        registered_programs = {
            (sr_code or "").strip(): normalize_program_name(program)
            for sr_code, program in c.fetchall()
            if (sr_code or "").strip()
        }
        c.execute(
            """
            SELECT program_name, program_code
            FROM programs
            WHERE program_name IS NOT NULL AND TRIM(program_name) <> ''
            """
        )
        known_programs = [
            (program_name, program_code)
            for program_name, program_code in c.fetchall()
            if normalize_program_name(program_name)
        ]
        known_programs.extend(
            (program, None)
            for program in registered_programs.values()
            if normalize_program_name(program)
        )
        program_lookup = build_program_lookup(known_programs)

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
            raw_program = (row.get(col_map["program"]) or "").strip() if col_map["program"] else ""
            year_level = (row.get(col_map["year_level"]) or "").strip() if col_map["year_level"] else ""
            registered_program = registered_programs.get(sr_code, "")
            resolved_program, resolution_status, candidates = resolve_program_name(
                raw_program,
                program_lookup,
                registered_program=registered_program,
            )

            stored_program = normalize_program_name(resolved_program)
            if resolution_status == "registered" and stored_program and normalize_program_name(raw_program) != stored_program:
                program_resolution["resolved_from_registration"] += 1
            elif resolution_status == "catalog" and stored_program and normalize_program_name(raw_program) != stored_program:
                program_resolution["resolved_from_catalog"] += 1
            elif resolution_status == "ambiguous" and is_program_code(raw_program):
                program_resolution["ambiguous_codes"] += 1
                stored_program = registered_program or ""
                if len(warnings_list) < 10:
                    warnings_list.append(
                        f"Row {index}: program code '{raw_program}' is ambiguous"
                        f" ({', '.join(candidates[:3])})."
                        " Use the full registered program name in the CSV to avoid misclassification."
                    )
            elif resolution_status == "unmatched" and is_program_code(raw_program):
                program_resolution["unmatched_codes"] += 1
                stored_program = registered_program or ""
                if len(warnings_list) < 10:
                    warnings_list.append(
                        f"Row {index}: program code '{raw_program}' could not be matched"
                        " to a registered full program."
                    )

            batch_rows.append((sr_code, name, gender, stored_program, year_level, parsed_timestamp, batch_id))
            inserted += 1

        if not batch_rows:
            conn.close()
            return jsonify(
                {
                    "success": False,
                    "message": "No valid rows found to import.",
                    "errors": errors[:10],
                    "warnings": warnings_list,
                }
            ), 400

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
        emit_analytics_update("import_logs", {"batch_id": batch_id, "inserted": inserted})

        return jsonify(
            {
                "success": True,
                "inserted": inserted,
                "skipped": skipped,
                "batch_id": batch_id,
                "total_imported": total_imported,
                "total_batches": total_batches,
                "errors": errors[:10],
                "warnings": warnings_list,
                "program_resolution": program_resolution,
                "message": (
                    f"Successfully imported {inserted} records."
                    + (
                        f" {program_resolution['ambiguous_codes'] + program_resolution['unmatched_codes']} row(s) need program review."
                        if program_resolution["ambiguous_codes"] or program_resolution["unmatched_codes"]
                        else ""
                    )
                ),
            }
        )

    @bp.route("/api/import-logs/summary", methods=["GET"], endpoint="api_import_logs_summary")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_import_logs_summary():
        conn = db_connect(deps["db_path"])
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
    @role_required("super_admin", "library_admin")
    def api_import_logs_delete(batch_id):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute("DELETE FROM imported_logs WHERE import_batch = ?", (batch_id,))
        deleted = c.rowcount
        conn.commit()
        conn.close()

        if deleted <= 0:
            return jsonify({"success": False, "message": "Import batch not found or already deleted."}), 404

        log_action("DELETE_IMPORT_BATCH", target=batch_id)
        emit_analytics_update("delete_import_batch", {"batch_id": batch_id, "deleted": deleted})
        return jsonify({"success": True, "deleted": deleted})

    @bp.route("/api/analytics-basic", methods=["GET"], endpoint="api_analytics_basic")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_analytics_basic():
        import sys
        print("DEBUG: api_analytics_basic() called", file=sys.stderr)
        try:
            from routes.ml_analytics import run_basic_analytics
            result = run_basic_analytics(deps["db_path"])
            print(f"DEBUG: run_basic_analytics returned: {type(result)}", file=sys.stderr)
            return jsonify(result)
        except Exception as e:
            print(f"DEBUG: api_analytics_basic error: {e}", file=sys.stderr)
            return jsonify({
                "message": f"Basic analytics failed: {e}",
                "details": str(e),
            }), 500

    @bp.route("/api/analytics-reports", methods=["GET"], endpoint="api_analytics_reports")

    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_analytics_reports():
        try:
            result = run_ml_analytics(deps["db_path"])
            status = 200
            return jsonify(result), status
        except Exception as e:
            return jsonify({
                "message": f"Analytics pipeline failed to run: {e}",
                "details": str(e),
            }), 500

    @bp.route("/api/events", methods=["GET"], endpoint="api_events")
    @bp.route("/api/entry-logs", methods=["GET"])
    @bp.route("/api/entry-exit-logs", methods=["GET"])
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_events():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT
                COALESCE(u.name, '-') AS name,
                COALESCE(e.sr_code, u.sr_code, '-') AS sr_code,
                COALESCE(e.confidence, 0.0) AS confidence,
                COALESCE(e.captured_at, e.ingested_at) AS event_time
            FROM recognition_events e
            LEFT JOIN users u ON e.user_id = u.user_id
            ORDER BY e.ingested_at DESC, e.id DESC
            LIMIT 500
            """
        )
        raw_logs = c.fetchall()
        if not raw_logs:
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

    @bp.route("/api/audit-log", methods=["GET"], endpoint="api_audit_log")
    @login_required
    @role_required("super_admin", "library_admin")
    def api_audit_log():
        limit = 500
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT audit_id, staff_id, username, action, target, ip_address, timestamp
            FROM audit_log
            ORDER BY timestamp DESC, audit_id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [
            {
                "audit_id": row[0],
                "staff_id": row[1],
                "username": row[2] or "",
                "action": row[3] or "",
                "target": row[4] or "",
                "ip_address": row[5] or "",
                "timestamp": row[6] or "",
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/program-monthly-visits", methods=["GET"], endpoint="api_program_monthly_visits")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_program_monthly_visits():
        payload = _monthly_program_visits_data(request.args.get("year", "").strip())
        return jsonify(payload)

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
        hidden_routes = {
            "/kiosk",
            "/kiosk-improved",
            "/api/kiosk-metrics",
            "/register",
            "/api/register-info",
        }
        visible_rules = [
            rule
            for rule in sorted(current_app.url_map.iter_rules(), key=lambda r: r.rule)
            if rule.rule not in hidden_routes
        ]
        for i, rule in enumerate(visible_rules, start=1):
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
    @role_required("super_admin", "library_admin", "library_staff")
    def api_policy():
        policy_html = deps["render_markdown_as_html"](Path("static/content/markdown/policy.md"))
        return jsonify({"policy": policy_html})

    @bp.route("/api/test-route", methods=["GET"], endpoint="api_test_route")
    def api_test_route():
        return jsonify({"message": "Test route works!"})

    return bp
