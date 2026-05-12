import os
import re
import struct
import csv
import io
import math
import time
import base64
import calendar
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import warnings
import uuid

import cv2
import numpy as np
from flask import Blueprint, flash, redirect, request, session, url_for, current_app, jsonify, send_from_directory

from auth import (
    create_staff,
    get_all_staff,
    api_login_required,
    api_role_required,
    log_action,
    login_required,
    role_required,
    toggle_staff_status,
)
from core.models import User
from core.config import QUALITY_CONTEXTS, QUALITY_PROFILE_BOUNDS, QUALITY_PROFILE_FIELDS
from core.program_catalog import (
    DEFAULT_COLLEGE_PROGRAM_MAP,
    OTHER_COLLEGE_LABEL,
    build_program_lookup,
    is_program_code,
    normalize_program_name,
    program_code_for,
    resolve_program_name,
)
from db import connect as db_connect, table_columns
from routes.ml_analytics import run_ml_analytics
from app.realtime import emit_analytics_update
from services.occupancy_service import OccupancyService, resolve_capacity_limit
from services.embedding_service import count_embeddings, merge_embeddings_by_model, normalize_embeddings_by_model
from services.versioning_service import bump_profiles_version, bump_settings_version, ensure_version_settings
from utils.image_utils import crop_face_region


def _normalize_date_key(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _normalize_timestamp_for_json(value, default=""):
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or default


def _normalize_user_type(value) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"enrolled", "unrecognized", "visitor", "staff"} else "unrecognized"


def _normalize_person_name(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return ""
    return normalized.upper()


_DASHBOARD_YEAR_LEVEL_LABELS = {
    1: "1st Year",
    2: "2nd Year",
    3: "3rd Year",
    4: "4th Year",
    5: "5th Year",
    6: "6th Year",
}
_DASHBOARD_YEAR_LEVEL_PATTERN = re.compile(r"(?P<prefix>\d{2})-\d{5}$")


def _derive_dashboard_year_level_from_sr_code(sr_code: str | None) -> str:
    normalized = str(sr_code or "").strip()
    match = _DASHBOARD_YEAR_LEVEL_PATTERN.fullmatch(normalized)
    if not match:
        return ""

    try:
        start_year = int(match.group("prefix"))
    except ValueError:
        return ""

    current_year = date.today().year % 100
    year_level = current_year - start_year
    return _DASHBOARD_YEAR_LEVEL_LABELS.get(year_level, "")


def _normalize_dashboard_year_level(
    sr_code: str | None,
    raw_year_level: str | None,
    *,
    program: str | None = None,
    user_type: str | None = None,
) -> str:
    normalized_user_type = str(user_type or "").strip().lower()
    normalized_program = str(program or "").strip().lower()
    normalized_sr_code = str(sr_code or "").strip().lower()
    visitor_like_sr_code = normalized_sr_code in {"", "-", "n/a", "na", "visitor", "unknown"}
    if normalized_user_type == "visitor" or normalized_program == "visitor" or visitor_like_sr_code:
        return "Visitor"

    derived = _derive_dashboard_year_level_from_sr_code(sr_code)
    if derived:
        return derived

    normalized = " ".join(str(raw_year_level or "").split())
    if not normalized:
        return "Unknown"

    lowered = normalized.lower().replace("-", " ")
    aliases = {
        "1": "1st Year",
        "1st": "1st Year",
        "1st year": "1st Year",
        "first year": "1st Year",
        "2": "2nd Year",
        "2nd": "2nd Year",
        "2nd year": "2nd Year",
        "second year": "2nd Year",
        "3": "3rd Year",
        "3rd": "3rd Year",
        "3rd year": "3rd Year",
        "third year": "3rd Year",
        "4": "4th Year",
        "4th": "4th Year",
        "4th year": "4th Year",
        "fourth year": "4th Year",
        "5": "5th Year",
        "5th": "5th Year",
        "5th year": "5th Year",
        "fifth year": "5th Year",
        "6": "6th Year",
        "6th": "6th Year",
        "6th year": "6th Year",
        "sixth year": "6th Year",
        "unknown:student": "Visitor",
        "unknown student": "Visitor",
        "visitor": "Visitor",
    }
    return aliases.get(lowered, normalized or "Unknown")


def _dashboard_year_level_sort_key(value: str | None) -> tuple[int, str]:
    normalized = " ".join((value or "").split())
    for number, label in _DASHBOARD_YEAR_LEVEL_LABELS.items():
        if normalized == label:
            return (number, label)
    if normalized == "Visitor":
        return (97, normalized)
    if normalized == "Unknown":
        return (99, normalized)
    return (98, normalized)


def _display_profile_field(value, *, user_type: str, default: str = "-", visitor_default: str = "Visitor") -> str:
    text = str(value or "").strip()
    if text:
        return text
    return visitor_default if _normalize_user_type(user_type) == "visitor" else default


def _excel_column_label(column_number: int) -> str:
    if column_number < 1:
        raise ValueError("Excel column numbers must be positive.")

    label = ""
    current = column_number
    while current:
        current, remainder = divmod(current - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _excel_column_number(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", str(cell_ref or ""))
    if not match:
        return 0

    value = 0
    for char in match.group(1):
        value = (value * 26) + (ord(char) - 64)
    return value


def _parse_payload_json_object(raw_value) -> dict:
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if isinstance(raw_value, memoryview):
        raw_value = raw_value.tobytes()
    if isinstance(raw_value, bytearray):
        raw_value = bytes(raw_value)
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8", errors="ignore")
    text = str(raw_value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def init_imported_logs_table(db_path):
    conn = db_connect(db_path)
    c = conn.cursor()
    if not table_columns(conn, "imported_logs"):
        conn.close()
        raise RuntimeError(
            "PostgreSQL schema is missing `imported_logs`. "
            "Run `alembic upgrade head` before starting the app."
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
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_import_batch
        ON imported_logs(import_batch)
        """
    )
    conn.commit()
    conn.close()


def create_routes_blueprint(deps):
    bp = Blueprint("routes", __name__)
    config = deps["config"]
    init_imported_logs_table(deps["db_path"])
    ensure_version_settings(deps["db_path"])

    def _utc_now_iso():
        return datetime.now(timezone.utc).isoformat()

    def _parse_iso_utc(value):
        if not value:
            return datetime.now(timezone.utc)
        text = str(value).strip()
        if not text:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _snapshot_recognition_event_identity(
        cursor,
        *,
        user_id: int,
        name: str | None,
        sr_code: str | None,
        gender: str | None,
        program: str | None,
        user_type: str | None,
    ) -> None:
        normalized_user_type = _normalize_user_type(user_type)
        fallback_sr = str(sr_code or "").strip()
        fallback_name = _display_profile_field(name, user_type=normalized_user_type)
        fallback_gender = _display_profile_field(gender, user_type=normalized_user_type)
        fallback_program = _display_profile_field(program, user_type=normalized_user_type)

        cursor.execute(
            """
            SELECT id, sr_code, payload_json
            FROM recognition_events
            WHERE user_id = %s
            """,
            (int(user_id),),
        )
        for event_row_id, event_sr_code, payload_raw in cursor.fetchall():
            payload = _parse_payload_json_object(payload_raw)
            resolved_sr_code = str(event_sr_code or "").strip() or fallback_sr
            if not resolved_sr_code and normalized_user_type == "visitor":
                resolved_sr_code = "Visitor"
            payload.update(
                {
                    "identity_name": fallback_name,
                    "identity_sr_code": resolved_sr_code or "-",
                    "identity_user_type": normalized_user_type,
                    "identity_gender": fallback_gender,
                    "identity_program": fallback_program,
                    "identity_snapshot_at": _utc_now_iso(),
                }
            )
            cursor.execute(
                """
                UPDATE recognition_events
                SET sr_code = %s, payload_json = %s
                WHERE id = %s
                """,
                (
                    resolved_sr_code or None,
                    json.dumps(payload, ensure_ascii=True),
                    int(event_row_id),
                ),
            )

    def _create_identity_user(
        *,
        name: str,
        sr_code: str | None,
        gender: str | None,
        program: str | None,
        user_type: str,
    ) -> int:
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        params = (
            name,
            sr_code,
            gender or "",
            program or "",
            b"",
            "",
            0,
            user_type,
        )
        c.execute(
            """
            INSERT INTO users (
                name, sr_code, gender, course, embeddings, image_paths, embedding_dim, user_type
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING user_id
            """,
            params,
        )
        row = c.fetchone()
        user_id = int(row[0])
        conn.commit()
        conn.close()
        return user_id

    def _record_manual_entry_event(
        *,
        user_id: int | None,
        sr_code: str | None,
        event_id: str,
        captured_at: datetime,
        method: str = "manual-resolution",
        resolution_action: str = "visitor_entry",
        metadata: dict | None = None,
    ):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        payload_json = {
            "event_id": event_id,
            "user_id": user_id,
            "sr_code": sr_code,
            "decision": "allowed",
            "source": "librarian_manual_resolution",
            "resolution_action": resolution_action,
            "resolution_metadata": metadata or {},
            "event_type": "entry",
            "captured_at": captured_at.isoformat(),
        }
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, method, captured_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id,
                user_id,
                sr_code,
                "allowed",
                "entry",
                method,
                captured_at,
                json.dumps(payload_json, ensure_ascii=True),
            ),
        )
        inserted = (c.rowcount or 0) > 0
        conn.commit()
        conn.close()
        if inserted:
            occupancy_service = OccupancyService(deps["db_path"])
            occupancy_state = occupancy_service.record_event("entry", captured_at)
            capacity_limit = resolve_capacity_limit(
                deps["db_path"],
                default=int(deps["config"].max_library_capacity),
            )
            occ_view = occupancy_service.get_current_occupancy(
                capacity_limit,
                warning_threshold=deps["config"].occupancy_warning_threshold,
            )
            emit_analytics_update(
                "registration_manual_entry",
                {
                    "event_id": event_id,
                    "user_id": user_id,
                    "daily_entries": occupancy_state["daily_entries"],
                    "daily_exits": occupancy_state["daily_exits"],
                    "occupancy_count": occupancy_state["occupancy_count"],
                    "capacity_warning": bool(occ_view["capacity_warning"]),
                },
            )
        return inserted

    def _record_manual_exit_event(
        *,
        user_id: int | None,
        sr_code: str | None,
        event_id: str,
        captured_at: datetime,
        method: str = "manual-resolution",
        resolution_action: str = "visitor_exit",
        metadata: dict | None = None,
    ):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        payload_json = {
            "event_id": event_id,
            "user_id": user_id,
            "sr_code": sr_code,
            "decision": "allowed",
            "source": "librarian_manual_resolution",
            "resolution_action": resolution_action,
            "resolution_metadata": metadata or {},
            "event_type": "exit",
            "captured_at": captured_at.isoformat(),
        }
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, method, captured_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id,
                user_id,
                sr_code,
                "allowed",
                "exit",
                method,
                captured_at,
                json.dumps(payload_json, ensure_ascii=True),
            ),
        )
        inserted = (c.rowcount or 0) > 0
        conn.commit()
        conn.close()
        if inserted:
            occupancy_service = OccupancyService(deps["db_path"])
            occupancy_state = occupancy_service.record_event("exit", captured_at)
            capacity_limit = resolve_capacity_limit(
                deps["db_path"],
                default=int(deps["config"].max_library_capacity),
            )
            occ_view = occupancy_service.get_current_occupancy(
                capacity_limit,
                warning_threshold=deps["config"].occupancy_warning_threshold,
            )
            emit_analytics_update(
                "registration_manual_exit",
                {
                    "event_id": event_id,
                    "user_id": user_id,
                    "daily_entries": occupancy_state["daily_entries"],
                    "daily_exits": occupancy_state["daily_exits"],
                    "occupancy_count": occupancy_state["occupancy_count"],
                    "capacity_warning": bool(occ_view["capacity_warning"]),
                },
            )
        return inserted

    def _visitor_presence_state(user_id: int) -> dict:
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT
                SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) AS entries,
                SUM(CASE WHEN event_type = 'exit' THEN 1 ELSE 0 END) AS exits
            FROM recognition_events
            WHERE user_id = %s
              AND DATE(COALESCE(captured_at, ingested_at)) = CURRENT_DATE
            """,
            (user_id,),
        )
        row = c.fetchone()
        conn.close()
        entries = int((row[0] if row else 0) or 0)
        exits = int((row[1] if row else 0) or 0)
        return {
            "entries_today": entries,
            "exits_today": exits,
            "inside_now": entries > exits,
        }

    def _resolve_registration_status_reason(reg_state):
        code = (getattr(reg_state, "status_reason_code", None) or "").strip() or None
        message = (getattr(reg_state, "status_reason_message", "") or "").strip()
        updated_at = (getattr(reg_state, "status_updated_at", None) or "").strip() or None
        if getattr(reg_state, "phase", "idle") == "expired":
            return (
                "session_expired",
                "Registration session expired due to inactivity. Start a new session to continue.",
                updated_at or _utc_now_iso(),
            )
        return code, message, updated_at

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
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except (ValueError, TypeError):
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
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
        samples = reg_state.captured_samples or []
        previews = []
        preview_limit = reg_state.total_retained_samples if reg_state.ready_to_submit else reg_state.max_captures
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
        reason_code, reason_message, reason_updated_at = _resolve_registration_status_reason(reg_state=reg_state)
        return {
            "active": progress.get("phase") in {"capturing", "ready"},
            "session_id": progress.get("session_id"),
            "phase": progress.get("phase", "idle"),
            "registration_kind": progress.get("registration_kind", "student"),
            "force_new_identity": bool(progress.get("force_new_identity")),
            "required_poses": progress["required_poses"],
            "current_pose": progress["current_pose"],
            "current_pose_index": progress["current_pose_index"],
            "pose_progress": progress["pose_progress"],
            "total_progress": progress["total_progress"],
            "ready_to_submit": progress["ready_to_submit"],
            "preview_samples": _registration_sample_previews(reg_state),
            "expires_in_seconds": progress.get("expires_in_seconds"),
            "status_reason_code": reason_code,
            "status_reason_message": reason_message,
            "status_updated_at": reason_updated_at,
        }

    def _registration_error_payload(reg_state, message: str, status_reason_code: str | None = None, **extra):
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](status_reason_code or "registration_error", message)
        payload = _registration_status_payload(reg_state)
        payload.update({"success": False, "message": message, **extra})
        return payload

    def _has_complete_pending_registration(reg_state) -> bool:
        if not deps["is_registration_ready"]():
            return False
        return len(reg_state.captured_samples or []) >= int(reg_state.total_retained_samples)

    def _validate_registration_fields(name: str, sr_code: str, gender: str, program: str, user_type: str = "enrolled"):
        allowed_genders = {"Male", "Female", "Other"}
        normalized_name = " ".join(name.split())
        normalized_program = " ".join(program.split())
        normalized_sr_code = sr_code.strip()

        if not normalized_name or not gender:
            return False, "Name and gender are required.", "name"

        if "," not in normalized_name:
            return False, "Use the name format: Last Name, First Name.", "name"

        last_name, first_name = [part.strip() for part in normalized_name.split(",", 1)]
        if not last_name or not first_name:
            return False, "Use the name format: Last Name, First Name.", "name"

        if not re.fullmatch(r"[A-Za-z][A-Za-z .,'-]{1,79}", normalized_name):
            return False, "Name contains invalid characters.", "name"

        if gender not in allowed_genders:
            return False, "Please select a valid gender.", "gender"

        if user_type == "visitor":
            if normalized_sr_code:
                if not re.fullmatch(r"\d{2}-\d{5}", normalized_sr_code):
                    return False, "SR Code must use the format 23-12345.", "sr_code"
            if normalized_program:
                if len(normalized_program) < 4 or len(normalized_program) > 120:
                    return False, "Program must be between 4 and 120 characters.", "program"
                if not re.fullmatch(r"[A-Za-z0-9&(),./' -]+", normalized_program):
                    return False, "Program contains invalid characters.", "program"
            return True, "", None

        if not normalized_sr_code or not normalized_program:
            return False, "Name, SR Code, gender, and program are required.", None

        if not re.fullmatch(r"\d{2}-\d{5}", normalized_sr_code):
            return False, "SR Code must use the format 23-12345.", "sr_code"

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
                    conf=float(getattr(deps["config"], "yolo_detection_confidence", 0.20)),
                    imgsz=int(getattr(deps["config"], "yolo_inference_imgsz", 960)),
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
                        face_crop, clamped_bbox = crop_face_region(image, x1, y1, x2, y2)
                        if face_crop is not None:
                            if clamped_bbox is not None:
                                x1, y1, x2, y2 = clamped_bbox
                            landmarks = None
                            keypoints_xy = getattr(getattr(result, "keypoints", None), "xy", None)
                            keypoints_conf = getattr(getattr(result, "keypoints", None), "conf", None)
                            if keypoints_xy is not None and best_index >= 0:
                                try:
                                    if hasattr(keypoints_xy, "detach"):
                                        keypoints_xy = keypoints_xy.detach().cpu().numpy()
                                    else:
                                        keypoints_xy = keypoints_xy.cpu().numpy() if hasattr(keypoints_xy, "cpu") else keypoints_xy
                                    if keypoints_conf is not None:
                                        if hasattr(keypoints_conf, "detach"):
                                            keypoints_conf = keypoints_conf.detach().cpu().numpy()
                                        else:
                                            keypoints_conf = keypoints_conf.cpu().numpy() if hasattr(keypoints_conf, "cpu") else keypoints_conf
                                    if best_index < len(keypoints_xy):
                                        points = keypoints_xy[best_index]
                                        point_conf = None
                                        if keypoints_conf is not None and best_index < len(keypoints_conf):
                                            point_conf = keypoints_conf[best_index]
                                        box_w = max(float(x2 - x1), 1.0)
                                        box_h = max(float(y2 - y1), 1.0)

                                        def _to_crop_pt(idx):
                                            if idx >= len(points):
                                                return None
                                            if point_conf is not None and idx < len(point_conf):
                                                kp_conf = float(point_conf[idx])
                                                if math.isnan(kp_conf) or kp_conf < 0.25:
                                                    return None
                                            px = float(points[idx][0])
                                            py = float(points[idx][1])
                                            if not math.isfinite(px) or not math.isfinite(py):
                                                return None
                                            cx = px - float(x1)
                                            cy = py - float(y1)
                                            if cx < (-0.10 * box_w) or cx > (1.10 * box_w):
                                                return None
                                            if cy < (-0.10 * box_h) or cy > (1.10 * box_h):
                                                return None
                                            return (
                                                min(max(cx, 0.0), box_w - 1.0),
                                                min(max(cy, 0.0), box_h - 1.0),
                                            )

                                        landmarks = {
                                            "left_eye": _to_crop_pt(0),
                                            "right_eye": _to_crop_pt(1),
                                            "nose": _to_crop_pt(2),
                                            "mouth_left": _to_crop_pt(3),
                                            "mouth_right": _to_crop_pt(4),
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
        if not table_columns(conn, "app_settings"):
            conn.close()
            raise RuntimeError(
                "PostgreSQL schema is missing `app_settings`. "
                "Run `alembic upgrade head` before starting the app."
            )
        conn.close()

    def _get_setting(db_path, key, default=None):
        _ensure_settings_table(db_path)
        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
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
            VALUES (%s, %s)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()
        conn.close()

    SETTINGS_BOUNDS = {
        "max_occupancy": {"min": 50, "max": 2000},
        "vector_index_top_k": {"min": 1, "max": 100},
        "threshold": {"min": 0.1, "max": 0.95},
        "primary_threshold": {"min": 0.1, "max": 0.95},
        "secondary_threshold": {"min": 0.1, "max": 0.95},
        "quality_threshold": {"min": 0.1, "max": 0.95},
        "face_quality_profiles": QUALITY_PROFILE_BOUNDS,
        "recognition_confidence_threshold": {"min": 0.1, "max": 0.99},
        "online_learning_confidence_threshold": {"min": 0.1, "max": 0.99},
        "occupancy_warning_threshold": {"min": 0.5, "max": 0.99},
        "occupancy_snapshot_interval_seconds": {"min": 60, "max": 3600},
        "recognition_event_retention_days": {"min": 1, "max": 3650},
    }
    SETTINGS_AUDIT_ACTION = "UPDATE_SETTINGS"
    SETTINGS_AUDIT_ROW_LIMIT = 25

    def _format_setting_value(field_name, value):
        if field_name in {
            "threshold",
            "primary_threshold",
            "secondary_threshold",
            "recognition_confidence_threshold",
            "online_learning_confidence_threshold",
        }:
            return f"{float(value):.3f}"
        if field_name in {"quality_threshold", "occupancy_warning_threshold"}:
            return f"{float(value):.2f}"
        return str(value)

    def _parse_bounded_int_payload(payload, key, minimum, maximum):
        if key not in payload:
            return None, None
        raw_value = str(payload.get(key, "")).strip()
        if not raw_value:
            return None, f"`{key}` is required."
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return None, f"Invalid `{key}` value."
        if parsed < minimum or parsed > maximum:
            return None, f"`{key}` must be between {minimum} and {maximum}."
        return parsed, None

    def _parse_bounded_float_payload(payload, key, minimum, maximum):
        if key not in payload:
            return None, None
        raw_value = str(payload.get(key, "")).strip()
        if not raw_value:
            return None, f"`{key}` is required."
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            return None, f"Invalid `{key}` value."
        if parsed < minimum or parsed > maximum:
            return None, f"`{key}` must be between {minimum} and {maximum}."
        return parsed, None

    def _quality_setting_key(context, field_name):
        return f"{context}_quality_{field_name}"

    def _coerce_quality_field(field_name, value):
        if field_name in {"quality_face_area_min", "quality_face_area_good"}:
            return int(value)
        return float(value)

    def _read_face_quality_profiles(legacy_quality_threshold=None):
        config = deps["config"]
        profiles = {}
        for context in QUALITY_CONTEXTS:
            base = config.quality_profile_for_context(context).to_dict()
            for field_name in QUALITY_PROFILE_FIELDS:
                fallback = (
                    legacy_quality_threshold
                    if field_name == "face_quality_threshold" and legacy_quality_threshold is not None
                    else base[field_name]
                )
                raw_value = _get_setting(
                    deps["db_path"],
                    _quality_setting_key(context, field_name),
                    fallback,
                )
                try:
                    parsed = _coerce_quality_field(field_name, raw_value)
                except (TypeError, ValueError):
                    parsed = fallback
                bounds = QUALITY_PROFILE_BOUNDS[field_name]
                parsed = max(bounds["min"], min(bounds["max"], parsed))
                base[field_name] = parsed
            profiles[context] = base
        config.apply_quality_profiles(profiles)
        return profiles

    def _parse_quality_profiles_payload(payload):
        if "face_quality_profiles" not in payload:
            return None, None
        raw_profiles = payload.get("face_quality_profiles")
        if not isinstance(raw_profiles, dict):
            return None, "`face_quality_profiles` must be an object."
        parsed_profiles = {}
        for context, profile_payload in raw_profiles.items():
            normalized_context = str(context or "").strip().lower()
            if normalized_context not in QUALITY_CONTEXTS:
                return None, f"`face_quality_profiles.{context}` is not a supported context."
            if not isinstance(profile_payload, dict):
                return None, f"`face_quality_profiles.{normalized_context}` must be an object."
            parsed_profile = {}
            for field_name, raw_value in profile_payload.items():
                if field_name not in QUALITY_PROFILE_FIELDS:
                    return None, f"`face_quality_profiles.{normalized_context}.{field_name}` is not supported."
                text = str(raw_value).strip()
                if not text:
                    return None, f"`face_quality_profiles.{normalized_context}.{field_name}` is required."
                try:
                    parsed = _coerce_quality_field(field_name, text)
                except (TypeError, ValueError):
                    return None, f"Invalid `face_quality_profiles.{normalized_context}.{field_name}` value."
                bounds = QUALITY_PROFILE_BOUNDS[field_name]
                if parsed < bounds["min"] or parsed > bounds["max"]:
                    return (
                        None,
                        f"`face_quality_profiles.{normalized_context}.{field_name}` must be between "
                        f"{bounds['min']} and {bounds['max']}.",
                    )
                parsed_profile[field_name] = parsed
            parsed_profiles[normalized_context] = parsed_profile
        return parsed_profiles, None

    def _parse_text_payload(payload, key, max_length=512):
        if key not in payload:
            return None, None
        raw_value = payload.get(key, "")
        text = str(raw_value or "").strip()
        if not text:
            return None, f"`{key}` is required."
        if len(text) > max_length:
            return None, f"`{key}` must be {max_length} characters or fewer."
        return text, None

    def _read_recognition_settings():
        config = deps["config"]

        def _coerce_float_value(raw_value, fallback):
            try:
                return float(raw_value)
            except (TypeError, ValueError):
                return float(fallback)

        def _coerce_int_value(raw_value, fallback):
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                return int(fallback)

        threshold_bounds = SETTINGS_BOUNDS["threshold"]
        threshold_setting = _get_setting(deps["db_path"], "threshold", str(deps["get_thresholds"]()[0]))
        threshold = _coerce_float_value(threshold_setting, deps["get_thresholds"]()[0])
        threshold = max(float(threshold_bounds["min"]), min(float(threshold_bounds["max"]), threshold))

        primary_bounds = SETTINGS_BOUNDS["primary_threshold"]
        primary_threshold_setting = _get_setting(
            deps["db_path"],
            "primary_threshold",
            str(config.primary_threshold),
        )
        primary_threshold = _coerce_float_value(primary_threshold_setting, config.primary_threshold)
        primary_threshold = max(
            float(primary_bounds["min"]),
            min(float(primary_bounds["max"]), primary_threshold),
        )
        config.primary_threshold = primary_threshold

        secondary_bounds = SETTINGS_BOUNDS["secondary_threshold"]
        secondary_threshold_setting = _get_setting(
            deps["db_path"],
            "secondary_threshold",
            str(config.secondary_threshold),
        )
        secondary_threshold = _coerce_float_value(secondary_threshold_setting, config.secondary_threshold)
        secondary_threshold = max(
            float(secondary_bounds["min"]),
            min(float(secondary_bounds["max"]), secondary_threshold),
        )
        config.secondary_threshold = secondary_threshold

        quality_bounds = SETTINGS_BOUNDS["quality_threshold"]
        quality_threshold_setting = _get_setting(
            deps["db_path"], "quality_threshold", str(deps["get_thresholds"]()[1])
        )
        quality_threshold = _coerce_float_value(quality_threshold_setting, deps["get_thresholds"]()[1])
        quality_threshold = max(
            float(quality_bounds["min"]),
            min(float(quality_bounds["max"]), quality_threshold),
        )
        deps["set_thresholds"](threshold, quality_threshold)

        confidence_bounds = SETTINGS_BOUNDS["recognition_confidence_threshold"]
        recognition_confidence_setting = _get_setting(
            deps["db_path"],
            "recognition_confidence_threshold",
            str(config.recognition_confidence_threshold),
        )
        recognition_confidence_threshold = _coerce_float_value(
            recognition_confidence_setting,
            config.recognition_confidence_threshold,
        )
        recognition_confidence_threshold = max(
            float(confidence_bounds["min"]),
            min(float(confidence_bounds["max"]), recognition_confidence_threshold),
        )

        learning_bounds = SETTINGS_BOUNDS["online_learning_confidence_threshold"]
        online_learning_confidence_setting = _get_setting(
            deps["db_path"],
            "online_learning_confidence_threshold",
            str(config.online_learning_confidence_threshold),
        )
        online_learning_confidence_threshold = _coerce_float_value(
            online_learning_confidence_setting,
            config.online_learning_confidence_threshold,
        )
        online_learning_confidence_threshold = max(
            float(learning_bounds["min"]),
            min(float(learning_bounds["max"]), online_learning_confidence_threshold),
        )
        config.online_learning_confidence_threshold = online_learning_confidence_threshold

        vector_bounds = SETTINGS_BOUNDS["vector_index_top_k"]
        vector_index_top_k_setting = _get_setting(
            deps["db_path"],
            "vector_index_top_k",
            str(config.vector_index_top_k),
        )
        vector_index_top_k = _coerce_int_value(vector_index_top_k_setting, config.vector_index_top_k)
        vector_index_top_k = max(
            int(vector_bounds["min"]),
            min(int(vector_bounds["max"]), vector_index_top_k),
        )
        config.vector_index_top_k = vector_index_top_k

        occ_bounds = SETTINGS_BOUNDS["max_occupancy"]
        max_occupancy_setting = _get_setting(
            deps["db_path"],
            "max_occupancy",
            str(config.max_library_capacity),
        )
        max_occupancy = _coerce_int_value(max_occupancy_setting, config.max_library_capacity)
        max_occupancy = max(
            int(occ_bounds["min"]),
            min(int(occ_bounds["max"]), max_occupancy),
        )
        config.max_library_capacity = max_occupancy

        warning_bounds = SETTINGS_BOUNDS["occupancy_warning_threshold"]
        warning_setting = _get_setting(
            deps["db_path"],
            "occupancy_warning_threshold",
            str(config.occupancy_warning_threshold),
        )
        occupancy_warning_threshold = _coerce_float_value(
            warning_setting,
            config.occupancy_warning_threshold,
        )
        occupancy_warning_threshold = max(
            float(warning_bounds["min"]),
            min(float(warning_bounds["max"]), occupancy_warning_threshold),
        )
        config.occupancy_warning_threshold = occupancy_warning_threshold

        interval_bounds = SETTINGS_BOUNDS["occupancy_snapshot_interval_seconds"]
        snapshot_setting = _get_setting(
            deps["db_path"],
            "occupancy_snapshot_interval_seconds",
            str(config.occupancy_snapshot_interval_seconds),
        )
        occupancy_snapshot_interval_seconds = _coerce_int_value(
            snapshot_setting,
            config.occupancy_snapshot_interval_seconds,
        )
        occupancy_snapshot_interval_seconds = max(
            int(interval_bounds["min"]),
            min(int(interval_bounds["max"]), occupancy_snapshot_interval_seconds),
        )
        config.occupancy_snapshot_interval_seconds = occupancy_snapshot_interval_seconds

        event_retention_bounds = SETTINGS_BOUNDS["recognition_event_retention_days"]
        event_retention_setting = _get_setting(
            deps["db_path"],
            "recognition_event_retention_days",
            str(getattr(config, "recognition_event_retention_days", 365)),
        )
        recognition_event_retention_days = _coerce_int_value(
            event_retention_setting,
            getattr(config, "recognition_event_retention_days", 365),
        )
        recognition_event_retention_days = max(
            int(event_retention_bounds["min"]),
            min(int(event_retention_bounds["max"]), recognition_event_retention_days),
        )
        config.recognition_event_retention_days = recognition_event_retention_days

        entry_source_setting = _get_setting(
            deps["db_path"],
            "entry_cctv_stream_source",
            str(config.entry_cctv_stream_source),
        )
        entry_cctv_stream_source = str(entry_source_setting or "").strip() or str(config.entry_cctv_stream_source)
        config.entry_cctv_stream_source = entry_cctv_stream_source

        exit_source_setting = _get_setting(
            deps["db_path"],
            "exit_cctv_stream_source",
            str(config.exit_cctv_stream_source),
        )
        exit_cctv_stream_source = str(exit_source_setting or "").strip() or str(config.exit_cctv_stream_source)
        config.exit_cctv_stream_source = exit_cctv_stream_source

        config.recognition_confidence_threshold = recognition_confidence_threshold
        face_quality_profiles = _read_face_quality_profiles(legacy_quality_threshold=quality_threshold)

        return {
            "threshold": float(threshold),
            "primary_threshold": float(primary_threshold),
            "secondary_threshold": float(secondary_threshold),
            "quality_threshold": float(quality_threshold),
            "recognition_confidence_threshold": float(recognition_confidence_threshold),
            "online_learning_confidence_threshold": float(online_learning_confidence_threshold),
            "vector_index_top_k": int(vector_index_top_k),
            "max_occupancy": int(max_occupancy),
            "occupancy_warning_threshold": float(occupancy_warning_threshold),
            "occupancy_snapshot_interval_seconds": int(occupancy_snapshot_interval_seconds),
            "recognition_event_retention_days": int(recognition_event_retention_days),
            "entry_cctv_stream_source": entry_cctv_stream_source,
            "exit_cctv_stream_source": exit_cctv_stream_source,
            "face_quality_profiles": face_quality_profiles,
        }

    def _read_settings_audit_rows(include_rows):
        if not include_rows:
            return [], None
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT audit_id, staff_id, username, action, target, ip_address, timestamp
            FROM audit_log
            WHERE action = %s
            ORDER BY timestamp DESC, audit_id DESC
            LIMIT %s
            """,
            (SETTINGS_AUDIT_ACTION, SETTINGS_AUDIT_ROW_LIMIT),
        )
        rows = [
            {
                "audit_id": row[0],
                "staff_id": row[1],
                "username": row[2] or "",
                "action": row[3] or "",
                "target": row[4] or "",
                "ip_address": row[5] or "",
                "timestamp": _normalize_timestamp_for_json(row[6]),
            }
            for row in c.fetchall()
        ]
        conn.close()
        if not rows:
            return rows, None
        latest = rows[0]
        return rows, {
            "audit_id": latest["audit_id"],
            "staff_id": latest["staff_id"],
            "username": latest["username"],
            "target": latest["target"],
            "timestamp": latest["timestamp"],
        }

    def _settings_permissions_for_role(role):
        role_name = str(role or "").strip().lower()
        return {
            "can_edit_thresholds": role_name == "super_admin",
            "can_edit_operational": role_name in {"super_admin", "library_admin"},
            "can_manage_advanced_ops": role_name == "super_admin",
            "can_view_audit": role_name in {"super_admin", "library_admin"},
            "can_save": role_name in {"super_admin", "library_admin"},
        }

    def _read_registered_user_count() -> int:
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        row = c.fetchone()
        conn.close()
        return int((row or [0])[0] or 0)

    def _build_settings_payload(role):
        role_name = str(role or "").strip().lower()
        permissions = _settings_permissions_for_role(role_name)
        settings_state = _read_recognition_settings()
        audit_rows, last_change = _read_settings_audit_rows(permissions["can_view_audit"])
        return {
            "role": role_name,
            "user_count": _read_registered_user_count(),
            "threshold": settings_state["threshold"],
            "primary_threshold": settings_state["primary_threshold"],
            "secondary_threshold": settings_state["secondary_threshold"],
            "quality_threshold": settings_state["quality_threshold"],
            "recognition_confidence_threshold": settings_state["recognition_confidence_threshold"],
            "online_learning_confidence_threshold": settings_state["online_learning_confidence_threshold"],
            "vector_index_top_k": settings_state["vector_index_top_k"],
            "max_occupancy": settings_state["max_occupancy"],
            "occupancy_warning_threshold": settings_state["occupancy_warning_threshold"],
            "occupancy_snapshot_interval_seconds": settings_state["occupancy_snapshot_interval_seconds"],
            "recognition_event_retention_days": settings_state["recognition_event_retention_days"],
            "entry_cctv_stream_source": settings_state["entry_cctv_stream_source"],
            "exit_cctv_stream_source": settings_state["exit_cctv_stream_source"],
            "face_quality_profiles": settings_state["face_quality_profiles"],
            "permissions": permissions,
            "bounds": SETTINGS_BOUNDS,
            "last_change": last_change,
            "audit_rows": audit_rows,
        }

    def _monthly_program_visits_data(selected_year=None):
        import calendar

        current_year = date.today().year
        try:
            year = int(selected_year or current_year)
        except (TypeError, ValueError):
            year = current_year

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        # ── 1. Available years ─────────────────────────────────────────────
        c.execute(
            """
            SELECT DISTINCT EXTRACT(YEAR FROM captured_at)::int AS yr
            FROM recognition_events
            WHERE captured_at IS NOT NULL
            ORDER BY yr DESC
            """
        )
        available_years = {current_year}
        for (raw_year,) in c.fetchall():
            try:
                available_years.add(int(raw_year))
            except:
                continue
        available_years = sorted(available_years, reverse=True)

        if year not in available_years:
            year = available_years[0] if available_years else current_year

        # ── 2. Seed programs (active only) ─────────────────────────────────
        c.execute(
            """
            SELECT program_name
            FROM programs
            WHERE program_name IS NOT NULL AND TRIM(program_name) <> ''
            ORDER BY program_name ASC
            """
        )
        program_names = [row[0] for row in c.fetchall() if row[0]]

        c.execute(
            """
            SELECT DISTINCT SUBSTR(CAST(captured_at AS TEXT), 1, 4) AS year
            FROM recognition_events
            WHERE captured_at IS NOT NULL AND TRIM(CAST(captured_at AS TEXT)) != ''
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
                COALESCE(NULLIF(TRIM(u.course), ''), 'Visitor') AS program,
                SUBSTR(CAST(re.captured_at AS TEXT), 6, 2) AS month_num,
                COUNT(*) AS visit_count
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
            AND EXTRACT(YEAR FROM re.captured_at)::int = %s
            AND COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') = 'entry'
            GROUP BY program, month_num
            ORDER BY program ASC, month_num ASC
            """,
            (year,),
        )

        raw_rows = c.fetchall()
        conn.close()

        # ── 4. Build grouped data ─────────────────────────────────────────
        grouped = {name: [0] * 12 for name in program_names}

        for program, month_num, visit_count in raw_rows:
            idx = int(month_num or 0) - 1
            if idx < 0 or idx > 11:
                continue

            if program not in grouped:
                grouped[program] = [0] * 12

            grouped[program][idx] = int(visit_count or 0)

        # ── 5. Build response ─────────────────────────────────────────────
        rows = []
        overall_monthly = [0] * 12

        for program in sorted(grouped):
            monthly_counts = grouped[program]
            total = sum(monthly_counts)

            for i, val in enumerate(monthly_counts):
                overall_monthly[i] += val

            rows.append({
                "program": program,
                "months": monthly_counts,
                "overall_total": total,
            })

        overall_row = {
            "program": "Overall Total",
            "months": overall_monthly,
            "overall_total": sum(overall_monthly),
        }

        return {
            "year": year,
            "years": available_years,
            "months": [calendar.month_abbr[i] for i in range(1, 13)],
            "rows": rows,
            "overall_row": overall_row,
        }

    def _available_visit_years(cursor):
        cursor.execute(
            """
            SELECT DISTINCT EXTRACT(YEAR FROM COALESCE(captured_at, ingested_at))::int AS year
            FROM recognition_events
            WHERE COALESCE(captured_at, ingested_at) IS NOT NULL
            ORDER BY year DESC
            """
        )
        years = []
        for (raw_year,) in cursor.fetchall():
            try:
                years.append(int(raw_year))
            except (TypeError, ValueError):
                continue
        return years

    def _resolve_daily_visit_category(raw_program, user_type, program_lookup, program_code_map):
        normalized_program = normalize_program_name(raw_program)
        normalized_user_type = _normalize_user_type(user_type)

        if normalized_program:
            resolved_program, resolution_status, _ = resolve_program_name(normalized_program, program_lookup)
            canonical_program = normalize_program_name(resolved_program)
            if resolution_status in {"catalog", "registered"} and canonical_program:
                return normalize_program_name(program_code_map.get(canonical_program)) or canonical_program
            return normalized_program

        if normalized_user_type == "visitor":
            return "Visitor"
        if normalized_user_type == "staff":
            return "Staff"
        return "Unassigned"

    def _monthly_daily_visits_data(selected_year=None, selected_month=None):
        current_date = date.today()
        try:
            year = int(selected_year or current_date.year)
        except (TypeError, ValueError):
            year = current_date.year

        try:
            month = int(selected_month or current_date.month)
        except (TypeError, ValueError):
            month = current_date.month

        if month < 1 or month > 12:
            month = current_date.month

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        c.execute(
            """
            SELECT program_name, program_code
            FROM programs
            WHERE program_name IS NOT NULL AND TRIM(program_name) <> ''
            """
        )
        known_program_rows = [
            (normalize_program_name(program_name), normalize_program_name(program_code))
            for program_name, program_code in c.fetchall()
            if normalize_program_name(program_name)
        ]
        program_lookup = build_program_lookup(known_program_rows)
        program_code_map = {
            program_name: (program_code or program_code_for(program_name))
            for program_name, program_code in known_program_rows
        }

        available_years = _available_visit_years(c)
        if year not in available_years:
            if available_years:
                year = available_years[0]
            else:
                available_years = [current_date.year]
                year = current_date.year

        days_in_month = calendar.monthrange(year, month)[1]

        c.execute(
            """
            SELECT
                re.user_id,
                COALESCE(NULLIF(TRIM(u.sr_code), ''), NULLIF(TRIM(re.sr_code), ''), '') AS identity_code,
                NULLIF(TRIM(u.course), '') AS raw_program,
                COALESCE(NULLIF(TRIM(u.user_type), ''), '') AS user_type,
                COALESCE(re.captured_at, re.ingested_at) AS event_time,
                COALESCE(NULLIF(TRIM(re.event_id), ''), '') AS event_id
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE COALESCE(re.captured_at, re.ingested_at) IS NOT NULL
              AND EXTRACT(YEAR FROM COALESCE(re.captured_at, re.ingested_at)) = %s
              AND EXTRACT(MONTH FROM COALESCE(re.captured_at, re.ingested_at)) = %s
            ORDER BY COALESCE(re.captured_at, re.ingested_at) ASC, re.id ASC
            """,
            (year, month),
        )
        raw_events = c.fetchall()
        conn.close()

        seen_daily_identities = set()
        grouped = {}
        for user_id, identity_code, raw_program, user_type, event_time, event_id in raw_events:
            day_key = _normalize_date_key(event_time)
            if not day_key:
                continue

            identity_value = ""
            if user_id is not None:
                identity_value = str(user_id).strip()
            if not identity_value:
                identity_value = str(identity_code or "").strip()
            if not identity_value:
                identity_value = str(event_id or "").strip()
            if not identity_value:
                continue

            visit_key = (identity_value, day_key)
            if visit_key in seen_daily_identities:
                continue
            seen_daily_identities.add(visit_key)

            try:
                day_number = int(day_key[-2:])
            except (TypeError, ValueError):
                continue
            if day_number < 1 or day_number > days_in_month:
                continue

            category_name = _resolve_daily_visit_category(raw_program, user_type, program_lookup, program_code_map)
            grouped.setdefault(category_name, [0] * days_in_month)[day_number - 1] += 1

        rows = []
        overall_daily = [0] * days_in_month
        for category_name in sorted(grouped):
            daily_counts = list(grouped.get(category_name, [0] * days_in_month))
            total_count = sum(daily_counts)
            for idx, count in enumerate(daily_counts):
                overall_daily[idx] += count
            rows.append(
                {
                    "category": category_name,
                    "days": daily_counts,
                    "overall_total": total_count,
                }
            )

        overall_row = {
            "category": "Overall Total",
            "days": overall_daily,
            "overall_total": sum(overall_daily),
        }

        return {
            "year": year,
            "month": month,
            "month_label": calendar.month_name[month],
            "years": available_years,
            "day_numbers": list(range(1, days_in_month + 1)),
            "rows": rows,
            "overall_row": overall_row,
            "month_options": [
                {"value": index, "label": calendar.month_name[index]}
                for index in range(1, 13)
            ],
        }

    def _xlsx_cell_child(cell, tag_name):
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        return cell.find(f"{namespace}{tag_name}")

    def _set_xlsx_text_cell(cell, text):
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        cell.attrib["t"] = "inlineStr"
        for child in list(cell):
            cell.remove(child)
        inline_string = ET.Element(f"{namespace}is")
        text_node = ET.SubElement(inline_string, f"{namespace}t")
        text_node.text = str(text)
        cell.append(inline_string)

    def _set_xlsx_number_cell(cell, value):
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        cell.attrib.pop("t", None)
        for child in list(cell):
            cell.remove(child)
        value_node = ET.Element(f"{namespace}v")
        value_node.text = str(int(value) if isinstance(value, bool) or isinstance(value, int) else value)
        cell.append(value_node)

    def _clear_xlsx_cell_value(cell):
        cell.attrib.pop("t", None)
        for child in list(cell):
            cell.remove(child)

    def _remove_xlsx_row_cells(row):
        for cell in list(row):
            row.remove(cell)

    def _clone_xlsx_row(row, row_number):
        cloned_row = ET.fromstring(ET.tostring(row))
        cloned_row.attrib["r"] = str(row_number)
        for cell in cloned_row.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
            cell_ref = str(cell.attrib.get("r") or "")
            match = re.match(r"([A-Z]+)", cell_ref)
            if not match:
                continue
            cell.attrib["r"] = f"{match.group(1)}{row_number}"
        return cloned_row

    def _sort_xlsx_row_cells(row):
        cells = list(row)
        cells.sort(key=lambda cell: _excel_column_number(cell.attrib.get("r")))
        for cell in cells:
            row.remove(cell)
        for cell in cells:
            row.append(cell)

    def _ensure_xlsx_cell(row, cell_ref, style_id=None):
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for cell in row.findall(f"{namespace}c"):
            if cell.attrib.get("r") == cell_ref:
                if style_id is not None:
                    cell.attrib["s"] = str(style_id)
                return cell

        cell = ET.Element(f"{namespace}c", {"r": cell_ref})
        if style_id is not None:
            cell.attrib["s"] = str(style_id)
        row.append(cell)
        _sort_xlsx_row_cells(row)
        return cell

    def _build_monthly_daily_visits_workbook(payload):
        from flask import make_response

        template_path = Path("static/report_templates/DAILY LIBRARY USERS PER MONTH TEMPLATE.xlsx")
        if not template_path.exists():
            raise FileNotFoundError("Monthly daily visits Excel template is missing.")

        rows = payload.get("rows") or []

        month_label = str(payload.get("month_label") or "").upper()
        year_label = str(payload.get("year") or "")
        day_numbers = list(payload.get("day_numbers") or [])
        total_column_number = len(day_numbers) + 2
        total_column_label = _excel_column_label(total_column_number)

        with zipfile.ZipFile(template_path, "r") as template_zip:
            sheet_xml = template_zip.read("xl/worksheets/sheet1.xml")
            styles_xml = template_zip.read("xl/styles.xml")
            workbook_xml = template_zip.read("xl/workbook.xml")
            worksheet = ET.fromstring(sheet_xml)
            styles_root = ET.fromstring(styles_xml)
            workbook_root = ET.fromstring(workbook_xml)
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            sheet_data = worksheet.find(f"{namespace}sheetData")
            if sheet_data is None:
                raise ValueError("Excel template worksheet is missing sheet data.")
            fonts = styles_root.find(f"{namespace}fonts")
            cell_xfs = styles_root.find(f"{namespace}cellXfs")
            if fonts is None or cell_xfs is None:
                raise ValueError("Excel template styles are missing required font definitions.")
            workbook_sheets = workbook_root.find(f"{namespace}sheets")
            if workbook_sheets is None or not list(workbook_sheets):
                raise ValueError("Excel template workbook is missing sheet definitions.")

            export_sheet_name = f"{payload['month_label']} {payload['year']}"[:31]
            workbook_sheets[0].attrib["name"] = export_sheet_name

            font_cache = {}
            style_cache = {}

            def clone_font(base_font_id, *, bold=None, size=None, font_name=None):
                cache_key = (base_font_id, bold, size, font_name)
                if cache_key in font_cache:
                    return font_cache[cache_key]

                base_font = fonts[int(base_font_id)]
                cloned_font = ET.fromstring(ET.tostring(base_font))
                bold_node = cloned_font.find(f"{namespace}b")
                if bold is True and bold_node is None:
                    cloned_font.insert(0, ET.Element(f"{namespace}b"))
                elif bold is False and bold_node is not None:
                    cloned_font.remove(bold_node)

                if size is not None:
                    size_node = cloned_font.find(f"{namespace}sz")
                    if size_node is None:
                        size_node = ET.SubElement(cloned_font, f"{namespace}sz")
                    size_node.attrib["val"] = str(size)

                if font_name is not None:
                    name_node = cloned_font.find(f"{namespace}name")
                    if name_node is None:
                        name_node = ET.SubElement(cloned_font, f"{namespace}name")
                    name_node.attrib["val"] = str(font_name)
                    scheme_node = cloned_font.find(f"{namespace}scheme")
                    if scheme_node is not None:
                        cloned_font.remove(scheme_node)

                fonts.append(cloned_font)
                fonts.attrib["count"] = str(len(fonts))
                font_id = len(fonts) - 1
                font_cache[cache_key] = font_id
                return font_id

            def clone_style(
                base_style_id,
                *,
                bold=None,
                wrap_text=None,
                horizontal=None,
                vertical=None,
                font_size=None,
                font_name=None,
                num_fmt_id=None,
            ):
                cache_key = (base_style_id, bold, wrap_text, horizontal, vertical, font_size, font_name, num_fmt_id)
                if cache_key in style_cache:
                    return style_cache[cache_key]

                base_style = cell_xfs[int(base_style_id)]
                cloned_style = ET.fromstring(ET.tostring(base_style))

                if bold is not None or font_size is not None or font_name is not None:
                    next_font_id = clone_font(
                        int(cloned_style.attrib.get("fontId", "0")),
                        bold=bold,
                        size=font_size,
                        font_name=font_name,
                    )
                    cloned_style.attrib["fontId"] = str(next_font_id)
                    cloned_style.attrib["applyFont"] = "1"

                if wrap_text is not None or horizontal is not None or vertical is not None:
                    alignment = cloned_style.find(f"{namespace}alignment")
                    if alignment is None:
                        alignment = ET.SubElement(cloned_style, f"{namespace}alignment")
                    if wrap_text is not None:
                        alignment.attrib["wrapText"] = "1" if wrap_text else "0"
                    if horizontal is not None:
                        alignment.attrib["horizontal"] = str(horizontal)
                    if vertical is not None:
                        alignment.attrib["vertical"] = str(vertical)
                    cloned_style.attrib["applyAlignment"] = "1"

                if num_fmt_id is not None:
                    cloned_style.attrib["numFmtId"] = str(num_fmt_id)
                    if str(num_fmt_id) == "0":
                        cloned_style.attrib.pop("applyNumberFormat", None)
                    else:
                        cloned_style.attrib["applyNumberFormat"] = "1"

                cell_xfs.append(cloned_style)
                cell_xfs.attrib["count"] = str(len(cell_xfs))
                style_id = len(cell_xfs) - 1
                style_cache[cache_key] = style_id
                return style_id

            rows_by_number = {
                int(row.attrib.get("r") or 0): row
                for row in sheet_data.findall(f"{namespace}row")
            }

            merge_cells = worksheet.find(f"{namespace}mergeCells")
            if merge_cells is not None:
                for merge_cell in merge_cells.findall(f"{namespace}mergeCell"):
                    ref = merge_cell.attrib.get("ref", "")
                    if ref.startswith("A") and ":Z" in ref:
                        merge_cell.attrib["ref"] = re.sub(r":Z(\d+)$", rf":{total_column_label}\1", ref)

            cols = worksheet.find(f"{namespace}cols")
            if cols is not None:
                existing_max = 0
                total_column_config = None
                for col in cols.findall(f"{namespace}col"):
                    try:
                        min_col = int(col.attrib.get("min") or 0)
                        max_col = int(col.attrib.get("max") or 0)
                    except (TypeError, ValueError):
                        continue
                    existing_max = max(existing_max, max_col)
                    if min_col <= total_column_number <= max_col:
                        total_column_config = col

                if existing_max < total_column_number:
                    for col_number in range(existing_max + 1, total_column_number):
                        ET.SubElement(
                            cols,
                            f"{namespace}col",
                            {
                                "min": str(col_number),
                                "max": str(col_number),
                                "width": "6.5",
                                "customWidth": "1",
                            },
                        )
                    total_column_config = ET.SubElement(
                        cols,
                        f"{namespace}col",
                        {
                            "min": str(total_column_number),
                            "max": str(total_column_number),
                            "width": "10.5",
                            "customWidth": "1",
                        },
                    )

                if total_column_config is not None:
                    total_column_config.attrib["width"] = "10.5"
                    total_column_config.attrib["customWidth"] = "1"

            title_cell = _ensure_xlsx_cell(rows_by_number[7], "A7")
            _set_xlsx_text_cell(title_cell, f"LIBRARY USERS for the MONTH of {month_label} {year_label}")
            title_row = rows_by_number[7]
            title_day_style = _ensure_xlsx_cell(title_row, "Y7").attrib.get("s")
            title_total_style = _ensure_xlsx_cell(title_row, "Z7").attrib.get("s")
            for column_number in range(26, total_column_number):
                title_fill_cell = _ensure_xlsx_cell(
                    title_row,
                    f"{_excel_column_label(column_number)}7",
                    title_day_style,
                )
                _clear_xlsx_cell_value(title_fill_cell)
            title_total_cell = _ensure_xlsx_cell(title_row, f"{total_column_label}7", title_total_style)
            _clear_xlsx_cell_value(title_total_cell)

            header_row = rows_by_number[8]
            label_header_style = _ensure_xlsx_cell(header_row, "A8").attrib.get("s")
            label_header_cell = _ensure_xlsx_cell(header_row, "A8", label_header_style)
            _set_xlsx_text_cell(label_header_cell, "COLLEGE/PROGRAM/OFFICE")
            day_header_style = _ensure_xlsx_cell(header_row, "Y8").attrib.get("s")
            total_header_style = _ensure_xlsx_cell(header_row, "Z8").attrib.get("s")
            for day_number in day_numbers:
                column_label = _excel_column_label(day_number + 1)
                header_cell = _ensure_xlsx_cell(header_row, f"{column_label}8", day_header_style)
                _set_xlsx_number_cell(header_cell, day_number)
            total_header_cell = _ensure_xlsx_cell(header_row, f"{total_column_label}8", total_header_style)
            _set_xlsx_text_cell(total_header_cell, "TOTAL")

            data_row_template = _clone_xlsx_row(rows_by_number[9], 9)
            total_row_template = _clone_xlsx_row(rows_by_number[36], 36)

            for existing_row in list(sheet_data.findall(f"{namespace}row")):
                try:
                    row_number = int(existing_row.attrib.get("r") or 0)
                except (TypeError, ValueError):
                    continue
                if row_number >= 9:
                    sheet_data.remove(existing_row)

            data_label_style = clone_style(
                int(_ensure_xlsx_cell(data_row_template, "A9").attrib.get("s") or 14),
                wrap_text=True,
                horizontal="center",
                vertical="center",
                font_size=11,
                font_name="Times New Roman",
            )
            total_label_style = clone_style(
                int(_ensure_xlsx_cell(total_row_template, "A36").attrib.get("s") or 31),
                bold=True,
                wrap_text=True,
                horizontal="center",
                vertical="center",
                font_size=11,
                font_name="Times New Roman",
            )
            total_column_style = clone_style(
                int(_ensure_xlsx_cell(data_row_template, "Z9").attrib.get("s") or 16),
                bold=True,
                horizontal="center",
                vertical="center",
                font_size=11,
                font_name="Times New Roman",
                num_fmt_id=0,
            )
            overall_row = payload.get("overall_row") or {"category": "Overall Total", "days": [], "overall_total": 0}
            overall_row_number = 9 + len(rows)

            total_row_style_cache = {}
            data_row_style_cache = {}

            def set_row_height(row, label_text):
                text_length = len(str(label_text or "").strip())
                approx_lines = max(1, math.ceil(text_length / 22)) if text_length else 1
                height = min(max(20, approx_lines * 15), 90)
                row.attrib["ht"] = str(height)
                row.attrib["customHeight"] = "1"

            def data_style_for_column(column_label):
                if column_label in data_row_style_cache:
                    return data_row_style_cache[column_label]

                template_ref = f"{column_label}9"
                template_cell = None
                for cell in data_row_template.findall(f"{namespace}c"):
                    if cell.attrib.get("r") == template_ref:
                        template_cell = cell
                        break
                base_style_id = int(template_cell.attrib.get("s") or 16) if template_cell is not None else 16
                wrapped = column_label == "A"
                style_id = clone_style(
                    base_style_id,
                    wrap_text=True if wrapped else None,
                    horizontal="center",
                    vertical="center",
                    font_size=11,
                    font_name="Times New Roman",
                    num_fmt_id=0 if not wrapped else None,
                )
                data_row_style_cache[column_label] = style_id
                return style_id

            def overall_style_for_column(column_label):
                if column_label in total_row_style_cache:
                    return total_row_style_cache[column_label]

                template_ref = f"{column_label}9"
                template_cell = None
                for cell in data_row_template.findall(f"{namespace}c"):
                    if cell.attrib.get("r") == template_ref:
                        template_cell = cell
                        break
                base_style_id = int(template_cell.attrib.get("s") or 16) if template_cell is not None else 16
                wrapped = column_label == "A"
                style_id = clone_style(
                    base_style_id,
                    bold=True,
                    wrap_text=True if wrapped else None,
                    horizontal="center",
                    vertical="center",
                    font_size=11,
                    font_name="Times New Roman",
                    num_fmt_id=0 if not wrapped else None,
                )
                total_row_style_cache[column_label] = style_id
                return style_id

            for row_number in range(9, overall_row_number + 1):
                row_index = row_number - 9
                is_total_row = row_number == overall_row_number
                sheet_row = _clone_xlsx_row(total_row_template if is_total_row else data_row_template, row_number)
                sheet_data.append(sheet_row)

                if is_total_row:
                    label_text = str(overall_row.get("category") or "Overall Total")
                    raw_days = list(overall_row.get("days") or [])
                    day_values = [
                        int(raw_days[index] or 0) if index < len(raw_days) else 0
                        for index in range(len(day_numbers))
                    ]
                    row_total_value = int(overall_row.get("overall_total") or 0)

                    label_cell = _ensure_xlsx_cell(sheet_row, f"A{row_number}", total_label_style)
                    _set_xlsx_text_cell(label_cell, label_text)
                    set_row_height(sheet_row, label_text)

                    for day_index, day_value in enumerate(day_values, start=1):
                        column_label = _excel_column_label(day_index + 1)
                        style_id = overall_style_for_column(column_label)
                        day_cell = _ensure_xlsx_cell(sheet_row, f"{column_label}{row_number}", style_id)
                        _set_xlsx_number_cell(day_cell, day_value)

                    total_cell = _ensure_xlsx_cell(
                        sheet_row,
                        f"{total_column_label}{row_number}",
                        overall_style_for_column(total_column_label),
                    )
                    _set_xlsx_number_cell(total_cell, row_total_value)
                else:
                    source_row = rows[row_index] or {}
                    label_text = str(source_row.get("category") or "")
                    raw_days = list(source_row.get("days") or [])
                    day_values = [
                        int(raw_days[index] or 0) if index < len(raw_days) else 0
                        for index in range(len(day_numbers))
                    ]
                    row_total_value = int(source_row.get("overall_total") or 0)

                    label_cell = _ensure_xlsx_cell(sheet_row, f"A{row_number}", data_label_style)
                    _set_xlsx_text_cell(label_cell, label_text)
                    set_row_height(sheet_row, label_text)

                    for day_index, day_value in enumerate(day_values, start=1):
                        column_label = _excel_column_label(day_index + 1)
                        day_cell = _ensure_xlsx_cell(
                            sheet_row,
                            f"{column_label}{row_number}",
                            data_style_for_column(column_label),
                        )
                        _set_xlsx_number_cell(day_cell, day_value)

                    total_cell = _ensure_xlsx_cell(sheet_row, f"{total_column_label}{row_number}", total_column_style)
                    _set_xlsx_number_cell(total_cell, row_total_value)

                for extra_column_number in range(total_column_number + 1, 34):
                    extra_cell = _ensure_xlsx_cell(sheet_row, f"{_excel_column_label(extra_column_number)}{row_number}")
                    _clear_xlsx_cell_value(extra_cell)

            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as exported_zip:
                for info in template_zip.infolist():
                    if info.filename == "xl/worksheets/sheet1.xml":
                        exported_zip.writestr(info, ET.tostring(worksheet, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/workbook.xml":
                        exported_zip.writestr(info, ET.tostring(workbook_root, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/styles.xml":
                        exported_zip.writestr(info, ET.tostring(styles_root, encoding="utf-8", xml_declaration=True))
                    else:
                        exported_zip.writestr(info, template_zip.read(info.filename))

        output.seek(0)
        response = make_response(output.getvalue())
        filename = f"daily_library_users_{payload['year']}_{payload['month']:02d}.xlsx"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return response

    def _build_program_monthly_visits_workbook(payload):
        from flask import make_response

        template_path = Path("static/report_templates/MONTHLY PROGRAM VISIT TEMPLATE.xlsx")
        if not template_path.exists():
            raise FileNotFoundError("Program monthly visits Excel template is missing.")

        rows = payload.get("rows") or []
        overall_row = payload.get("overall_row") or {"program": "Overall Total", "months": [0] * 12, "overall_total": 0}
        month_labels = list(payload.get("months") or [])
        if len(month_labels) != 12:
            month_labels = [calendar.month_abbr[idx] for idx in range(1, 13)]

        with zipfile.ZipFile(template_path, "r") as template_zip:
            sheet_xml = template_zip.read("xl/worksheets/sheet1.xml")
            styles_xml = template_zip.read("xl/styles.xml")
            workbook_xml = template_zip.read("xl/workbook.xml")
            worksheet = ET.fromstring(sheet_xml)
            styles_root = ET.fromstring(styles_xml)
            workbook_root = ET.fromstring(workbook_xml)
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            sheet_data = worksheet.find(f"{namespace}sheetData")
            if sheet_data is None:
                raise ValueError("Excel template worksheet is missing sheet data.")
            fonts = styles_root.find(f"{namespace}fonts")
            cell_xfs = styles_root.find(f"{namespace}cellXfs")
            if fonts is None or cell_xfs is None:
                raise ValueError("Excel template styles are missing required format definitions.")

            workbook_sheets = workbook_root.find(f"{namespace}sheets")
            if workbook_sheets is None or not list(workbook_sheets):
                raise ValueError("Excel template workbook is missing sheet definitions.")

            workbook_sheets[0].attrib["name"] = str(payload.get("year") or "Program Visits")[:31]

            rows_by_number = {
                int(row.attrib.get("r") or 0): row
                for row in sheet_data.findall(f"{namespace}row")
            }
            required_rows = {7, 8, 9, 10, 36, 37, 38, 39}
            missing_rows = sorted(row_number for row_number in required_rows if row_number not in rows_by_number)
            if missing_rows:
                raise ValueError(f"Excel template is missing required row(s): {missing_rows}")

            title_cell = _ensure_xlsx_cell(rows_by_number[7], "A7")
            _set_xlsx_text_cell(title_cell, f"LIBRARY USERS for the YEAR {payload['year']}")

            header_row = rows_by_number[8]
            program_header_cell = _ensure_xlsx_cell(header_row, "A8")
            _set_xlsx_text_cell(program_header_cell, "COLLEGE/PROGRAM/OFFICE")
            for month_index, month_label in enumerate(month_labels, start=2):
                month_cell = _ensure_xlsx_cell(header_row, f"{_excel_column_label(month_index)}8")
                _set_xlsx_text_cell(month_cell, month_label)
            total_header_cell = _ensure_xlsx_cell(header_row, "N8")
            _set_xlsx_text_cell(total_header_cell, "TOTAL")

            first_data_row_template = _clone_xlsx_row(rows_by_number[9], 9)
            data_row_template = _clone_xlsx_row(rows_by_number[10], 10)
            total_row_template = _clone_xlsx_row(rows_by_number[36], 36)
            footer_row_templates = [
                _clone_xlsx_row(rows_by_number[37], 37),
                _clone_xlsx_row(rows_by_number[38], 38),
                _clone_xlsx_row(rows_by_number[39], 39),
            ]
            program_column_style = _ensure_xlsx_cell(first_data_row_template, "A9").attrib.get("s")
            font_style_cache = {}
            xf_style_cache = {}

            def clone_font(base_font_id, *, bold=False):
                cache_key = (int(base_font_id or 0), bool(bold))
                if cache_key in font_style_cache:
                    return font_style_cache[cache_key]

                base_font = fonts[cache_key[0]]
                cloned_font = ET.fromstring(ET.tostring(base_font))
                bold_node = cloned_font.find(f"{namespace}b")
                if bold and bold_node is None:
                    cloned_font.insert(0, ET.Element(f"{namespace}b"))
                elif not bold and bold_node is not None:
                    cloned_font.remove(bold_node)

                fonts.append(cloned_font)
                fonts.attrib["count"] = str(len(fonts))
                font_id = len(fonts) - 1
                font_style_cache[cache_key] = font_id
                return font_id

            def clone_numeric_style(base_style_id, *, bold=False, horizontal="center", vertical="center"):
                cache_key = (int(base_style_id or 0), bool(bold), horizontal, vertical)
                if cache_key in xf_style_cache:
                    return xf_style_cache[cache_key]

                base_style = cell_xfs[cache_key[0]]
                cloned_style = ET.fromstring(ET.tostring(base_style))
                cloned_style.attrib["numFmtId"] = "0"
                cloned_style.attrib.pop("applyNumberFormat", None)
                alignment = cloned_style.find(f"{namespace}alignment")
                if alignment is None:
                    alignment = ET.SubElement(cloned_style, f"{namespace}alignment")
                alignment.attrib["horizontal"] = str(horizontal)
                alignment.attrib["vertical"] = str(vertical)
                cloned_style.attrib["applyAlignment"] = "1"
                if bold:
                    next_font_id = clone_font(int(cloned_style.attrib.get("fontId", "0")), bold=True)
                    cloned_style.attrib["fontId"] = str(next_font_id)
                    cloned_style.attrib["applyFont"] = "1"

                cell_xfs.append(cloned_style)
                cell_xfs.attrib["count"] = str(len(cell_xfs))
                style_id = len(cell_xfs) - 1
                xf_style_cache[cache_key] = style_id
                return style_id

            def clone_text_style(base_style_id, *, bold=False, horizontal="center", vertical="center"):
                cache_key = (f"text:{int(base_style_id or 0)}", bool(bold), horizontal, vertical)
                if cache_key in xf_style_cache:
                    return xf_style_cache[cache_key]

                base_style = cell_xfs[int(base_style_id or 0)]
                cloned_style = ET.fromstring(ET.tostring(base_style))
                alignment = cloned_style.find(f"{namespace}alignment")
                if alignment is None:
                    alignment = ET.SubElement(cloned_style, f"{namespace}alignment")
                alignment.attrib["horizontal"] = str(horizontal)
                alignment.attrib["vertical"] = str(vertical)
                cloned_style.attrib["applyAlignment"] = "1"
                if bold:
                    next_font_id = clone_font(int(cloned_style.attrib.get("fontId", "0")), bold=True)
                    cloned_style.attrib["fontId"] = str(next_font_id)
                    cloned_style.attrib["applyFont"] = "1"

                cell_xfs.append(cloned_style)
                cell_xfs.attrib["count"] = str(len(cell_xfs))
                style_id = len(cell_xfs) - 1
                xf_style_cache[cache_key] = style_id
                return style_id

            centered_program_style = clone_text_style(int(program_column_style or 13))

            month_cell_styles = {}
            for month_index in range(12):
                column_label = _excel_column_label(month_index + 2)
                base_style = _ensure_xlsx_cell(first_data_row_template, f"{column_label}9").attrib.get("s") or "14"
                month_cell_styles[column_label] = clone_numeric_style(int(base_style))
            total_column_style = clone_numeric_style(
                int(_ensure_xlsx_cell(first_data_row_template, "N9").attrib.get("s") or 17),
                bold=True,
            )
            overall_month_cell_styles = {}
            for month_index in range(12):
                column_label = _excel_column_label(month_index + 2)
                base_style = _ensure_xlsx_cell(total_row_template, f"{column_label}36").attrib.get("s") or "14"
                overall_month_cell_styles[column_label] = clone_numeric_style(int(base_style), bold=True)
            overall_total_style = clone_numeric_style(
                int(_ensure_xlsx_cell(total_row_template, "N36").attrib.get("s") or 17),
                bold=True,
            )
            overall_program_style = clone_text_style(
                int(_ensure_xlsx_cell(total_row_template, "A36").attrib.get("s") or 33),
                bold=True,
            )

            def to_export_code(value):
                normalized = normalize_program_name(value)
                lowered = normalized.lower()
                if not normalized:
                    return "N/A"
                if lowered == "overall total":
                    return "Overall Total"
                if lowered == "visitor":
                    return "VIS"
                if lowered == "staff":
                    return "STAFF"
                if lowered == "unassigned":
                    return "UNASSIGNED"
                if is_program_code(normalized):
                    return normalized.upper()
                return normalize_program_name(program_code_for(normalized)) or normalized.upper()

            for existing_row in list(sheet_data.findall(f"{namespace}row")):
                try:
                    row_number = int(existing_row.attrib.get("r") or 0)
                except (TypeError, ValueError):
                    continue
                if row_number >= 9:
                    sheet_data.remove(existing_row)

            def set_row_height(sheet_row, label_text):
                text_length = len(str(label_text or "").strip())
                approx_lines = max(1, math.ceil(text_length / 22)) if text_length else 1
                height = min(max(20, approx_lines * 15), 60)
                sheet_row.attrib["ht"] = str(height)
                sheet_row.attrib["customHeight"] = "1"

            def set_program_cell(sheet_row, row_number, value, style_id):
                program_cell = _ensure_xlsx_cell(sheet_row, f"A{row_number}", style_id)
                if value is None or str(value).strip() == "":
                    _clear_xlsx_cell_value(program_cell)
                else:
                    _set_xlsx_text_cell(program_cell, str(value))

            for row_index, row in enumerate(rows, start=1):
                row_number = 8 + row_index
                row_template = first_data_row_template if row_index == 1 else data_row_template
                sheet_row = _clone_xlsx_row(row_template, row_number)
                sheet_data.append(sheet_row)

                export_label = to_export_code(row.get("program"))
                set_program_cell(sheet_row, row_number, export_label, centered_program_style)
                set_row_height(sheet_row, export_label)

                month_values = list(row.get("months") or [])
                for month_index in range(12):
                    column_label = _excel_column_label(month_index + 2)
                    month_cell = _ensure_xlsx_cell(sheet_row, f"{column_label}{row_number}", month_cell_styles[column_label])
                    _set_xlsx_number_cell(month_cell, int(month_values[month_index] or 0) if month_index < len(month_values) else 0)

                total_cell = _ensure_xlsx_cell(sheet_row, f"N{row_number}", total_column_style)
                _set_xlsx_number_cell(total_cell, int(row.get("overall_total") or 0))

            total_row_number = 9 + len(rows)
            total_row = _clone_xlsx_row(total_row_template, total_row_number)
            sheet_data.append(total_row)
            overall_label = to_export_code(overall_row.get("program") or "Overall Total")
            set_program_cell(total_row, total_row_number, overall_label, overall_program_style)
            set_row_height(total_row, overall_label)

            overall_months = list(overall_row.get("months") or [])
            for month_index in range(12):
                column_label = _excel_column_label(month_index + 2)
                month_cell = _ensure_xlsx_cell(total_row, f"{column_label}{total_row_number}", overall_month_cell_styles[column_label])
                _set_xlsx_number_cell(month_cell, int(overall_months[month_index] or 0) if month_index < len(overall_months) else 0)

            total_cell = _ensure_xlsx_cell(total_row, f"N{total_row_number}", overall_total_style)
            _set_xlsx_number_cell(total_cell, int(overall_row.get("overall_total") or 0))

            footer_start_row = total_row_number + 1
            for offset, footer_template in enumerate(footer_row_templates):
                sheet_data.append(_clone_xlsx_row(footer_template, footer_start_row + offset))

            merge_cells = worksheet.find(f"{namespace}mergeCells")
            if merge_cells is not None:
                footer_row_offset = footer_start_row - 37
                for merge_cell in merge_cells.findall(f"{namespace}mergeCell"):
                    ref = str(merge_cell.attrib.get("ref") or "")
                    match = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", ref)
                    if not match:
                        continue
                    start_column, start_row, end_column, end_row = match.groups()
                    start_row_number = int(start_row)
                    end_row_number = int(end_row)
                    if start_row_number >= 37 and end_row_number >= 37:
                        merge_cell.attrib["ref"] = (
                            f"{start_column}{start_row_number + footer_row_offset}:"
                            f"{end_column}{end_row_number + footer_row_offset}"
                        )

            cols = worksheet.find(f"{namespace}cols")
            if cols is not None:
                for col in cols.findall(f"{namespace}col"):
                    try:
                        min_col = int(col.attrib.get("min") or 0)
                        max_col = int(col.attrib.get("max") or 0)
                    except (TypeError, ValueError):
                        continue
                    if min_col >= 2 and max_col <= 13:
                        col.attrib["width"] = "10.5"
                        col.attrib["customWidth"] = "1"
                    elif min_col <= 14 and max_col >= 14:
                        col.attrib["width"] = "11.5"
                        col.attrib["customWidth"] = "1"

            dimension = worksheet.find(f"{namespace}dimension")
            if dimension is not None:
                dimension.attrib["ref"] = f"A1:N{footer_start_row + len(footer_row_templates) - 1}"

            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as exported_zip:
                for info in template_zip.infolist():
                    if info.filename == "xl/worksheets/sheet1.xml":
                        exported_zip.writestr(info, ET.tostring(worksheet, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/styles.xml":
                        exported_zip.writestr(info, ET.tostring(styles_root, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/workbook.xml":
                        exported_zip.writestr(info, ET.tostring(workbook_root, encoding="utf-8", xml_declaration=True))
                    else:
                        exported_zip.writestr(info, template_zip.read(info.filename))

        output.seek(0)
        response = make_response(output.getvalue())
        filename = f"program_monthly_visits_{payload['year']}.xlsx"
        response.headers["Content-Disposition"] = f"attachment; filename={filename}"
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return response

    def _build_entry_exit_logs_workbook(log_rows, *, selected_date=None, filename_date=None):
        from flask import make_response

        template_path = Path("static/report_templates/LIBRARY USERS VISITS TEMPLATE.xlsx")
        if not template_path.exists():
            raise FileNotFoundError("Entry and exit logs Excel template is missing.")

        normalized_selected_date = str(selected_date or "").strip()

        with zipfile.ZipFile(template_path, "r") as template_zip:
            content_types_xml = template_zip.read("[Content_Types].xml")
            sheet_xml = template_zip.read("xl/worksheets/sheet1.xml")
            sheet_rels_xml = template_zip.read("xl/worksheets/_rels/sheet1.xml.rels")
            workbook_xml = template_zip.read("xl/workbook.xml")
            workbook_rels_xml = template_zip.read("xl/_rels/workbook.xml.rels")
            content_types_root = ET.fromstring(content_types_xml)
            worksheet = ET.fromstring(sheet_xml)
            worksheet_rels_root = ET.fromstring(sheet_rels_xml)
            workbook_root = ET.fromstring(workbook_xml)
            workbook_rels_root = ET.fromstring(workbook_rels_xml)
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            package_namespace = "{http://schemas.openxmlformats.org/package/2006/content-types}"
            rels_namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
            sheet_data = worksheet.find(f"{namespace}sheetData")
            if sheet_data is None:
                raise ValueError("Excel template worksheet is missing sheet data.")

            workbook_sheets = workbook_root.find(f"{namespace}sheets")
            if workbook_sheets is None or not list(workbook_sheets):
                raise ValueError("Excel template workbook is missing sheet definitions.")

            rows_by_number = {
                int(row.attrib.get("r") or 0): row
                for row in sheet_data.findall(f"{namespace}row")
            }
            required_rows = {9, 10, 36, 37, 38, 39}
            missing_rows = sorted(row_number for row_number in required_rows if row_number not in rows_by_number)
            if missing_rows:
                raise ValueError(f"Excel template is missing required row(s): {missing_rows}")

            first_data_row_template = _clone_xlsx_row(rows_by_number[9], 9)
            data_row_template = _clone_xlsx_row(rows_by_number[10], 10)
            footer_row_templates = [
                _clone_xlsx_row(rows_by_number[37], 37),
                _clone_xlsx_row(rows_by_number[38], 38),
                _clone_xlsx_row(rows_by_number[39], 39),
            ]
            name_column_style = _ensure_xlsx_cell(first_data_row_template, "D9").attrib.get("s")

            def set_template_cell_value(sheet_row, column_label, row_number, value):
                cell = _ensure_xlsx_cell(sheet_row, f"{column_label}{row_number}")
                if value is None or str(value).strip() == "":
                    _clear_xlsx_cell_value(cell)
                    return
                if column_label == "A" and isinstance(value, (int, float)):
                    _set_xlsx_number_cell(cell, int(value))
                    return
                _set_xlsx_text_cell(cell, str(value))

            def set_row_height(sheet_row, log_row):
                max_length = max(
                    len(str(log_row.get("name") or "").strip()),
                    len(str(log_row.get("college_office") or "").strip()),
                    len(str(log_row.get("program") or "").strip()),
                )
                approx_lines = max(1, math.ceil(max_length / 22)) if max_length else 1
                height = min(max(20, approx_lines * 15), 60)
                sheet_row.attrib["ht"] = str(height)
                sheet_row.attrib["customHeight"] = "1"

            def render_sheet(log_rows_for_sheet, sheet_name):
                worksheet_copy = ET.fromstring(ET.tostring(worksheet))
                sheet_data_copy = worksheet_copy.find(f"{namespace}sheetData")
                if sheet_data_copy is None:
                    raise ValueError("Excel template worksheet is missing sheet data.")

                for existing_row in list(sheet_data_copy.findall(f"{namespace}row")):
                    try:
                        row_number = int(existing_row.attrib.get("r") or 0)
                    except (TypeError, ValueError):
                        continue
                    if row_number >= 9:
                        sheet_data_copy.remove(existing_row)

                for row_index, log_row in enumerate(log_rows_for_sheet, start=1):
                    row_number = 8 + row_index
                    row_template = first_data_row_template if row_index == 1 else data_row_template
                    sheet_row = _clone_xlsx_row(row_template, row_number)
                    sheet_data_copy.append(sheet_row)
                    set_row_height(sheet_row, log_row)

                    set_template_cell_value(sheet_row, "A", row_number, row_index)
                    set_template_cell_value(sheet_row, "B", row_number, log_row.get("date"))
                    set_template_cell_value(sheet_row, "C", row_number, log_row.get("sr_code"))
                    name_cell = _ensure_xlsx_cell(sheet_row, f"D{row_number}", name_column_style)
                    if log_row.get("name") is None or str(log_row.get("name")).strip() == "":
                        _clear_xlsx_cell_value(name_cell)
                    else:
                        _set_xlsx_text_cell(name_cell, str(log_row.get("name")))
                    set_template_cell_value(sheet_row, "E", row_number, log_row.get("sex"))
                    set_template_cell_value(sheet_row, "F", row_number, log_row.get("college_office"))
                    set_template_cell_value(sheet_row, "G", row_number, log_row.get("program"))
                    set_template_cell_value(sheet_row, "H", row_number, log_row.get("entry_timestamp"))
                    set_template_cell_value(sheet_row, "I", row_number, log_row.get("exit_timestamp"))

                footer_start_row = 9 + len(log_rows_for_sheet)
                for offset, footer_template in enumerate(footer_row_templates):
                    sheet_data_copy.append(_clone_xlsx_row(footer_template, footer_start_row + offset))

                merge_cells = worksheet_copy.find(f"{namespace}mergeCells")
                if merge_cells is not None:
                    footer_row_offset = footer_start_row - 37
                    for merge_cell in merge_cells.findall(f"{namespace}mergeCell"):
                        ref = str(merge_cell.attrib.get("ref") or "")
                        match = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", ref)
                        if not match:
                            continue
                        start_column, start_row, end_column, end_row = match.groups()
                        start_row_number = int(start_row)
                        end_row_number = int(end_row)
                        if start_row_number >= 37 and end_row_number >= 37:
                            merge_cell.attrib["ref"] = (
                                f"{start_column}{start_row_number + footer_row_offset}:"
                                f"{end_column}{end_row_number + footer_row_offset}"
                            )

                dimension = worksheet_copy.find(f"{namespace}dimension")
                if dimension is not None:
                    dimension.attrib["ref"] = f"A1:I{footer_start_row + len(footer_row_templates) - 1}"

                return {
                    "name": str(sheet_name or "Attendance Log")[:31],
                    "worksheet": worksheet_copy,
                }

            if normalized_selected_date:
                rendered_sheets = [
                    render_sheet(log_rows, f"Attendance {normalized_selected_date}")
                ]
            else:
                grouped_rows = {}
                for log_row in log_rows:
                    date_key = str(log_row.get("date") or "").strip() or "Undated"
                    grouped_rows.setdefault(date_key, []).append(log_row)
                rendered_sheets = [
                    render_sheet(grouped_rows[date_key], date_key)
                    for date_key in sorted(grouped_rows.keys(), reverse=True)
                ] or [render_sheet([], "Attendance Log")]

            for existing_sheet in list(workbook_sheets):
                workbook_sheets.remove(existing_sheet)

            worksheet_relationships = [
                rel
                for rel in workbook_rels_root.findall(f"{rels_namespace}Relationship")
                if rel.attrib.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
            ]
            for rel in worksheet_relationships:
                workbook_rels_root.remove(rel)

            for override in list(content_types_root.findall(f"{package_namespace}Override")):
                if str(override.attrib.get("PartName") or "").startswith("/xl/worksheets/sheet"):
                    content_types_root.remove(override)

            existing_rel_ids = []
            for rel in workbook_rels_root.findall(f"{rels_namespace}Relationship"):
                rel_id = str(rel.attrib.get("Id") or "")
                match = re.match(r"rId(\d+)$", rel_id)
                if match:
                    existing_rel_ids.append(int(match.group(1)))
            next_rel_id = max(existing_rel_ids, default=0) + 1

            for index, rendered_sheet in enumerate(rendered_sheets, start=1):
                rel_id = f"rId{next_rel_id}"
                next_rel_id += 1
                sheet_file = f"sheet{index}.xml"

                ET.SubElement(
                    workbook_sheets,
                    f"{namespace}sheet",
                    {
                        "name": rendered_sheet["name"],
                        "sheetId": str(index),
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id": rel_id,
                    },
                )
                ET.SubElement(
                    workbook_rels_root,
                    f"{rels_namespace}Relationship",
                    {
                        "Id": rel_id,
                        "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                        "Target": f"worksheets/{sheet_file}",
                    },
                )
                ET.SubElement(
                    content_types_root,
                    f"{package_namespace}Override",
                    {
                        "PartName": f"/xl/worksheets/{sheet_file}",
                        "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
                    },
                )

            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as exported_zip:
                for info in template_zip.infolist():
                    if info.filename == "[Content_Types].xml":
                        exported_zip.writestr(info, ET.tostring(content_types_root, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/worksheets/sheet1.xml":
                        exported_zip.writestr(
                            info,
                            ET.tostring(rendered_sheets[0]["worksheet"], encoding="utf-8", xml_declaration=True),
                        )
                    elif info.filename == "xl/worksheets/_rels/sheet1.xml.rels":
                        exported_zip.writestr(info, ET.tostring(worksheet_rels_root, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/workbook.xml":
                        exported_zip.writestr(info, ET.tostring(workbook_root, encoding="utf-8", xml_declaration=True))
                    elif info.filename == "xl/_rels/workbook.xml.rels":
                        exported_zip.writestr(info, ET.tostring(workbook_rels_root, encoding="utf-8", xml_declaration=True))
                    else:
                        exported_zip.writestr(info, template_zip.read(info.filename))

                for index, rendered_sheet in enumerate(rendered_sheets[1:], start=2):
                    exported_zip.writestr(
                        f"xl/worksheets/sheet{index}.xml",
                        ET.tostring(rendered_sheet["worksheet"], encoding="utf-8", xml_declaration=True),
                    )
                    exported_zip.writestr(
                        f"xl/worksheets/_rels/sheet{index}.xml.rels",
                        ET.tostring(worksheet_rels_root, encoding="utf-8", xml_declaration=True),
                    )

        output.seek(0)
        response = make_response(output.getvalue())
        export_name = str(filename_date or date.today().strftime("%m-%d-%Y"))
        response.headers["Content-Disposition"] = (
            f"attachment; filename=library_entry_logs_{export_name}.xlsx"
        )
        response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return response

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
        warnings.warn(
            "Dashboard analytics now use recognition_events (canonical event model). See docs/database_schema_policy.md",
            DeprecationWarning,
            stacklevel=2,
        )
        filter_window = _dashboard_filter_window(filter_key)
        start_date = filter_window["start_date"]
        end_date = filter_window["end_date"]
        range_params = (start_date.isoformat(), end_date.isoformat())

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        # ── Existing queries (kept exactly as before) ──────────────

        c.execute("""
            SELECT COUNT(*)
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) BETWEEN %s AND %s
        """, range_params)
        total_logs = c.fetchone()[0]

        c.execute("""
                        SELECT COUNT(*) FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) = CURRENT_DATE
        """)
        today_logs = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(DISTINCT user_id)
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) BETWEEN %s AND %s
        """, range_params)
        unique_visitors = c.fetchone()[0]

        c.execute("""
            SELECT confidence
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) BETWEEN %s AND %s
        """, range_params)
        conf_values = []
        low_confidence_count = 0
        for (confidence,) in c.fetchall():
            value = _coerce_confidence(confidence)
            if value is not None:
                conf_values.append(value)
                if value < 0.7:
                    low_confidence_count += 1
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else None

        max_occupancy_setting = _get_setting(deps["db_path"], "max_occupancy", "300")
        try:
            max_occupancy = int(max_occupancy_setting)
        except (TypeError, ValueError):
            max_occupancy = 300
        occupancy_service = OccupancyService(deps["db_path"])
        current_occupancy = occupancy_service.get_current_occupancy(max_occupancy)["occupancy_count"]
        occupancy_remaining = max(max_occupancy - current_occupancy, 0)
        occupancy_ratio = (current_occupancy / max_occupancy) if max_occupancy else 0
        if occupancy_ratio >= config.occupancy_warning_threshold:
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
                        SELECT DATE(captured_at) as day, COUNT(*) as count
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) BETWEEN %s AND %s
            GROUP BY day
            ORDER BY day ASC
        """, range_params)
        date_map = {}
        for day_value, count in c.fetchall():
            normalized_day = _normalize_date_key(day_value)
            if not normalized_day:
                continue
            date_map[normalized_day] = int(count or 0)
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
            SELECT u.course, COUNT(DISTINCT re.user_id) as count
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
              AND u.course IS NOT NULL
              AND u.course != ''
              AND DATE(re.captured_at) BETWEEN %s AND %s
            GROUP BY u.course
            ORDER BY count DESC
            LIMIT 8
        """, range_params)
        program_distribution = [
            {"program": row[0], "count": row[1]}
            for row in c.fetchall()
        ]

        c.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(u.gender), ''), 'Unknown') AS gender,
                COUNT(DISTINCT re.user_id) AS count
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
              AND re.user_id IS NOT NULL
              AND DATE(re.captured_at) BETWEEN %s AND %s
            GROUP BY gender
            ORDER BY count DESC, gender ASC
        """, range_params)
        gender_distribution = [
            {"gender": row[0], "count": row[1]}
            for row in c.fetchall()
        ]

        c.execute("""
            SELECT
                re.user_id,
                NULLIF(TRIM(u.sr_code), '') AS sr_code,
                NULLIF(TRIM(u.course), '') AS program,
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'enrolled') AS user_type,
                MAX(NULLIF(TRIM(i.year_level), '')) AS year_level
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            LEFT JOIN imported_logs i
              ON NULLIF(TRIM(i.sr_code), '') = NULLIF(TRIM(u.sr_code), '')
            WHERE re.captured_at IS NOT NULL
              AND re.user_id IS NOT NULL
              AND DATE(re.captured_at) BETWEEN %s AND %s
            GROUP BY
                re.user_id,
                NULLIF(TRIM(u.sr_code), ''),
                NULLIF(TRIM(u.course), ''),
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'enrolled')
        """, range_params)
        year_level_counts = {}
        for _, sr_code, program, user_type, raw_year_level in c.fetchall():
            label = _normalize_dashboard_year_level(
                sr_code,
                raw_year_level,
                program=program,
                user_type=user_type,
            )
            year_level_counts[label] = year_level_counts.get(label, 0) + 1
        year_level_distribution = sorted(
            [
                {"year_level": label, "count": count}
                for label, count in year_level_counts.items()
            ],
            key=lambda item: _dashboard_year_level_sort_key(item.get("year_level")),
        )

        # ── Peak hours (24-slot array, index = hour) ───────────────

        c.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') AS event_type,
                COUNT(*) AS count
            FROM recognition_events re
            WHERE re.captured_at IS NOT NULL
              AND DATE(re.captured_at) BETWEEN %s AND %s
            GROUP BY event_type
        """, range_params)
        event_type_totals = {}
        for event_type, count in c.fetchall():
            normalized_event_type = str(event_type or "").strip().lower() or "entry"
            event_type_totals[normalized_event_type] = int(count or 0)
        total_entries = event_type_totals.get("entry", 0)
        total_exits = event_type_totals.get("exit", 0)

        c.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                COUNT(*) AS count
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
              AND DATE(re.captured_at) BETWEEN %s AND %s
            GROUP BY user_type
        """, range_params)
        user_type_totals = {
            "enrolled": 0,
            "visitor": 0,
            "unrecognized": 0,
            "staff": 0,
        }
        for user_type, count in c.fetchall():
            normalized_user_type = _normalize_user_type(user_type)
            user_type_totals[normalized_user_type] += int(count or 0)
        user_type_distribution = [
            {"label": "Enrolled", "count": user_type_totals["enrolled"], "accent": "green"},
            {"label": "Visitor", "count": user_type_totals["visitor"], "accent": "blue"},
            {"label": "Unrecognized", "count": user_type_totals["unrecognized"], "accent": "amber"},
            {"label": "Staff", "count": user_type_totals["staff"], "accent": "rose"},
        ]
        unrecognized_count = user_type_totals["unrecognized"]

        c.execute("""
                        SELECT EXTRACT(HOUR FROM captured_at)::int AS hour,
                   COUNT(*) as count
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) BETWEEN %s AND %s
            GROUP BY hour
        """, range_params)
        hour_map = {row[0]: row[1] for row in c.fetchall()}
        peak_hours = [hour_map.get(h, 0) for h in range(24)]

        # ── Top 10 frequent visitors ───────────────────────────────

        c.execute("""
                        SELECT
                            COALESCE(NULLIF(TRIM(u.name), ''), 'Visitor') AS name,
                            COALESCE(NULLIF(TRIM(u.sr_code), ''), NULLIF(TRIM(re.sr_code), ''), 'Visitor') AS sr_code,
                            COUNT(re.id) as visits
                        FROM recognition_events re
                        LEFT JOIN users u ON re.user_id = u.user_id
                        WHERE re.captured_at IS NOT NULL
                            AND DATE(re.captured_at) BETWEEN %s AND %s
                        GROUP BY
                            re.user_id,
                            COALESCE(NULLIF(TRIM(u.name), ''), 'Visitor'),
                            COALESCE(NULLIF(TRIM(u.sr_code), ''), NULLIF(TRIM(re.sr_code), ''), 'Visitor')
            ORDER BY visits DESC
            LIMIT 10
        """, range_params)
        top_visitors = [
            {"name": row[0], "sr_code": row[1], "visits": row[2]}
            for row in c.fetchall()
        ]
        
        # ── Weekly Heatmap (Day 0=Mon to 6=Sun, Hours 7AM–7PM) ──
        c.execute("""
            SELECT
                re.id,
                COALESCE(NULLIF(TRIM(u.name), ''), '') AS name,
                COALESCE(NULLIF(TRIM(u.sr_code), ''), NULLIF(TRIM(re.sr_code), ''), '') AS sr_code,
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                COALESCE(re.confidence, 0.0) AS confidence,
                COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') AS event_type,
                COALESCE(re.captured_at, re.ingested_at) AS event_time,
                COALESCE(re.decision, 'allowed') AS decision
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
              AND DATE(re.captured_at) BETWEEN %s AND %s
              AND COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') = 'entry'
            ORDER BY event_time DESC, re.id DESC
            LIMIT 6
        """, range_params)
        recent_entries = []
        for row_id, name, sr_code, user_type, confidence, event_type, event_time, decision in c.fetchall():
            normalized_user_type = _normalize_user_type(user_type)
            confidence_value = _coerce_confidence(confidence) or 0.0
            recent_entries.append(
                {
                    "id": row_id,
                    "name": _display_profile_field(name, user_type=normalized_user_type),
                    "sr_code": _display_profile_field(sr_code, user_type=normalized_user_type),
                    "user_type": normalized_user_type,
                    "event_type": str(event_type or "").strip().lower() or "entry",
                    "status": str(decision or "allowed").strip().lower() or "allowed",
                    "conf_pct": int(confidence_value * 100.0),
                    "timestamp": _normalize_timestamp_for_json(event_time),
                }
            )

        # PostgreSQL EXTRACT(DOW) returns 0=Sun,1=Mon,...6=Sat.
        # Remap to Mon=0 ... Sun=6.
        c.execute("""
            SELECT
                CASE EXTRACT(DOW FROM captured_at)::int
                    WHEN 0 THEN 6
                    ELSE EXTRACT(DOW FROM captured_at)::int - 1
                END as day_of_week,
                EXTRACT(HOUR FROM captured_at)::int AS hour,
                COUNT(*) as count
            FROM recognition_events
            WHERE captured_at IS NOT NULL
              AND EXTRACT(HOUR FROM captured_at)::int BETWEEN 7 AND 19
              AND DATE(captured_at) BETWEEN %s AND %s
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
                TO_CHAR(captured_at, 'YYYY-MM') AS month,
                COUNT(*) as count
            FROM recognition_events
            WHERE captured_at IS NOT NULL
              AND DATE(captured_at) BETWEEN %s AND %s
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
            "total_entries": total_entries,
            "total_exits": total_exits,
            "unrecognized_count": unrecognized_count,
            "low_confidence_count": low_confidence_count,
            "daily_visitors": daily_visitors,
            "program_distribution": program_distribution,
            "gender_distribution": gender_distribution,
            "year_level_distribution": year_level_distribution,
            "user_type_distribution": user_type_distribution,
            "peak_hours": peak_hours,
            "top_visitors": top_visitors,
            "recent_entries": recent_entries,
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

    @bp.route("/monthly-daily-visits", endpoint="monthly_daily_visits_page")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def monthly_daily_visits_page():
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

    @bp.route("/settings", methods=["GET"], endpoint="settings")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def settings():
        return _spa_index()

    @bp.route("/registered-profiles", endpoint="registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def registered_profiles():
        return _spa_index()

    @bp.route("/archive-profiles", endpoint="registered_profiles_archive")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def registered_profiles_archive():
        return _spa_index()

    @bp.route("/archive-profiles/submit", methods=["POST"], endpoint="registered_profiles_archive_submit")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
            WHERE user_id IN ({",".join("%s" for _ in user_ids)})
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
    @role_required("super_admin", "library_admin", "library_staff")
    def archived_profiles():
        return _spa_index()

    @bp.route("/archived-profiles/restore", methods=["POST"], endpoint="archived_profiles_restore")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
            WHERE user_id IN ({",".join("%s" for _ in user_ids)})
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
        warnings.warn(
            "analytics_reports now uses recognition_events (canonical event model). See docs/database_schema_policy.md",
            DeprecationWarning,
            stacklevel=2,
        )
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

        c.execute("SELECT COUNT(*) FROM recognition_events")
        total_logs = c.fetchone()[0]

        c.execute(
            """
                        SELECT COUNT(*) FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) = CURRENT_DATE
            """
        )
        today_logs = c.fetchone()[0]

        c.execute(
            """
            SELECT COUNT(DISTINCT user_id)
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) = CURRENT_DATE
            """
        )
        today_unique = c.fetchone()[0]

        c.execute("SELECT confidence FROM recognition_events WHERE captured_at IS NOT NULL")
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
        if occupancy_ratio >= config.occupancy_warning_threshold:
            occupancy_status = "Approaching capacity"
        elif occupancy_ratio >= 0.7:
            occupancy_status = "Moderately busy"
        else:
            occupancy_status = "Available"

        start_date = date.today() - timedelta(days=range_days - 1)
        c.execute(
            """
                        SELECT DATE(captured_at) as day,
                   confidence
                        FROM recognition_events
                        WHERE captured_at IS NOT NULL
                            AND DATE(captured_at) >= %s
            """,
            (start_date.isoformat(),),
        )
        daily_counts_map = {}
        daily_conf_sum = {}
        daily_conf_n = {}
        for day, confidence in c.fetchall():
            day_key = _normalize_date_key(day)
            if not day_key:
                continue
            daily_counts_map[day_key] = daily_counts_map.get(day_key, 0) + 1
            value = _coerce_confidence(confidence)
            if value is not None:
                daily_conf_sum[day_key] = daily_conf_sum.get(day_key, 0.0) + value
                daily_conf_n[day_key] = daily_conf_n.get(day_key, 0) + 1

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
            LEFT JOIN recognition_events r ON u.user_id = r.user_id
            WHERE r.captured_at IS NOT NULL
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
    @api_login_required
    @api_role_required("super_admin")
    def reset_database():
        conn = None
        try:
            conn = db_connect(deps["db_path"])
            c = conn.cursor()
            # Delete dependent/child tables before parent `users` to satisfy FK constraints.
            delete_order = [
                "recognition_events",
                "user_embeddings",
                "users",
            ]
            for table_name in delete_order:
                if table_columns(conn, table_name):
                    c.execute(f"DELETE FROM {table_name}")
            conn.commit()
            conn.close()

            deps["reset_database_state"]()
            return {"success": True, "message": "Database reset successfully"}
        except Exception as e:
            try:
                if conn is not None:
                    conn.rollback()
                    conn.close()
            except Exception:
                pass
            return {"success": False, "message": str(e)}, 500

    @bp.route("/api/clear_log", methods=["POST"], endpoint="clear_log")
    @api_login_required
    @api_role_required("super_admin")
    def clear_log():
        conn = None
        try:
            conn = db_connect(deps["db_path"])
            c = conn.cursor()
            c.execute("DELETE FROM recognition_events")
            conn.commit()
            conn.close()
            return {"success": True, "message": "Recognition events cleared"}
        except Exception as e:
            try:
                if conn is not None:
                    conn.rollback()
                    conn.close()
            except Exception:
                pass
            return {"success": False, "message": str(e)}, 500

    @bp.route("/api/detection/pause", methods=["POST"], endpoint="api_detection_pause")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_detection_pause():
        deps["pause_detection"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "detection_paused",
                "Detection paused while website registration uses the camera.",
            )
        return jsonify({"success": True, "detection_paused": True})

    @bp.route("/api/detection/resume", methods=["POST"], endpoint="api_detection_resume")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_detection_resume():
        deps["resume_detection"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "detection_resumed",
                "Detection resumed and camera monitoring is active.",
            )
        return jsonify({"success": True, "detection_paused": False})

    def _record_manual_resolution_event(
        *,
        event_id: str,
        decision: str,
        action: str,
        captured_at: datetime,
        user_id: int | None = None,
        sr_code: str | None = None,
        event_type: str = "entry",
        metadata: dict | None = None,
    ) -> None:
        payload_json = {
            "event_id": event_id,
            "user_id": user_id,
            "sr_code": sr_code,
            "decision": decision,
            "event_type": event_type,
            "source": "librarian_manual_resolution",
            "resolution_action": action,
            "resolution_metadata": metadata or {},
            "captured_at": captured_at.isoformat(),
        }
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, method, captured_at, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id,
                user_id,
                sr_code,
                decision,
                event_type,
                "manual-resolution",
                captured_at,
                json.dumps(payload_json, ensure_ascii=True),
            ),
        )
        conn.commit()
        conn.close()

    @bp.route("/api/registrations/active", methods=["GET"], endpoint="api_registration_active_get")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_active_get():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        return jsonify(_registration_status_payload(reg_state))

    @bp.route("/api/registrations/active", methods=["POST"], endpoint="api_registration_active_start")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_active_start():
        deps["expire_registration_session_if_needed"]()
        payload = request.get_json(silent=True) or {}
        registration_kind = str(payload.get("registration_kind") or "student").strip().lower()
        start_session = deps.get("start_registration_session")
        if callable(start_session):
            started = bool(start_session(registration_kind))
        else:
            started = bool(deps["start_web_registration_session"]())
        reg_state = deps["get_registration_state"]()
        status_payload = _registration_status_payload(reg_state)
        if not started:
            status_payload.update(
                {
                    "success": False,
                    "message": "A registration session is already active. Submit or cancel it first.",
                }
            )
            return jsonify(status_payload), 409
        status_payload.update(
            {
                "success": True,
                "message": "Registration session started.",
            }
        )
        return jsonify(status_payload), 201

    @bp.route("/api/registrations/active", methods=["DELETE"], endpoint="api_registration_active_cancel")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_active_cancel():
        cancel_session = deps.get("cancel_registration_session")
        if callable(cancel_session):
            cancel_session(reason_code="session_canceled", reason_message="Registration session canceled.")
        else:
            deps["cancel_web_registration_session"]()
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        payload.update({"success": True, "message": "Registration session canceled."})
        return jsonify(payload)

    @bp.route("/api/registrations/active/override", methods=["POST"], endpoint="api_registration_active_override")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_active_override():
        deps["expire_registration_session_if_needed"]()
        override_session = deps.get("override_registration_session")
        if callable(override_session):
            overridden = bool(override_session())
        else:
            deps["enable_unknown_registration_override"]()
            overridden = True
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        if not overridden:
            payload.update({"success": False, "message": "No active registration session to override."})
            return jsonify(payload), 409
        payload.update({"success": True, "message": "Manual override enabled for this session."})
        return jsonify(payload)

    @bp.route("/api/registrations/active/submit", methods=["POST"], endpoint="api_registration_active_submit")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_active_submit():
        deps["expire_registration_session_if_needed"]()
        repository = deps["repository"]
        reg_state = deps["get_registration_state"]()
        pending_registration = reg_state.captured_samples or []
        if not pending_registration:
            return jsonify({"success": False, "message": "No captured registration samples found."}), 400
        if not _has_complete_pending_registration(reg_state):
            return jsonify(
                {
                    "success": False,
                    "message": "Registration capture is not complete yet. Finish all required face samples before saving.",
                }
            ), 400

        payload = request.get_json(silent=True) or {}
        registration_kind = str(payload.get("registration_kind") or reg_state.registration_kind or "student").strip().lower()
        if registration_kind not in {"student", "visitor"}:
            registration_kind = "student"
        user_type = "visitor" if registration_kind == "visitor" else "enrolled"

        name = str(payload.get("name") or "").strip()
        sr_code = str(payload.get("sr_code") or "").strip() if registration_kind != "visitor" else str(payload.get("sr_code") or "").strip()
        if registration_kind == "visitor" and sr_code == "":
            sr_code = None
        gender = str(payload.get("gender") or "").strip()
        program = str(payload.get("program") or "").strip()

        is_valid, validation_message, invalid_field = _validate_registration_fields(
            name,
            sr_code or "",
            gender,
            program,
            user_type=user_type,
        )
        if not is_valid:
            return jsonify({"success": False, "message": validation_message, "field": invalid_field}), 400

        if user_type == "enrolled":
            existing = repository.get_user_by_sr_code(sr_code)
            if existing is not None:
                return jsonify(
                    {
                        "success": False,
                        "message": f"SR Code {sr_code} is already registered to {existing.name}. Use a different SR Code.",
                    }
                ), 409

        all_embeddings = {}
        for face_sample in pending_registration:
            all_embeddings = merge_embeddings_by_model(all_embeddings, face_sample.embeddings)

        user_id = repository.save_user(
            User(
                id=0,
                name=name,
                sr_code=sr_code,
                gender=gender,
                program=program,
                user_type=user_type,
                embeddings=all_embeddings,
                image_paths=[],
                embedding_dim=0,
            )
        )
        bump_profiles_version(deps["db_path"])
        saved_user = repository.get_user_by_id(user_id)
        if saved_user:
            deps["replace_user"](saved_user)

        deps["complete_registration"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "registration_submitted",
                f"Profile registered for {saved_user.name}." if saved_user else "Registration saved successfully.",
            )
        total_embeddings = count_embeddings(normalize_embeddings_by_model(all_embeddings))
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
            }
        )

    @bp.route("/api/registrations/resolutions", methods=["POST"], endpoint="api_registration_resolutions")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_registration_resolutions():
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"unrecognized_approve", "unrecognized_deny", "visitor_entry", "visitor_exit"}:
            return jsonify({"success": False, "message": "`action` is invalid."}), 400

        performer = str(session.get("username") or session.get("role") or "staff")
        captured_at = _parse_iso_utc(payload.get("captured_at"))
        notes = str(payload.get("notes") or "").strip()
        source_event_id = str(payload.get("event_id") or "").strip() or None

        if action == "unrecognized_deny":
            denied_event_id = f"resolution-{uuid.uuid4().hex}"
            _record_manual_resolution_event(
                event_id=denied_event_id,
                decision="denied",
                action=action,
                captured_at=captured_at,
                user_id=None,
                sr_code=None,
                event_type="entry",
                metadata={
                    "source_event_id": source_event_id,
                    "performed_by": performer,
                    "notes": notes or None,
                },
            )
            emit_analytics_update("unrecognized_denied", {"event_id": source_event_id, "resolution_event_id": denied_event_id})
            return jsonify(
                {
                    "success": True,
                    "action": action,
                    "event_id": source_event_id,
                    "resolution_event_id": denied_event_id,
                    "status": "denied",
                }
            )

        if action == "unrecognized_approve":
            name = str(payload.get("name") or "").strip()
            if not name:
                return jsonify({"success": False, "message": "`name` is required for unrecognized approval."}), 400
            sr_code = str(payload.get("sr_code") or "").strip() or None
            gender = str(payload.get("gender") or "").strip() or ""
            program = str(payload.get("program") or "").strip() or ""
            resolved_type = str(payload.get("user_type") or "unrecognized").strip().lower()
            if resolved_type not in {"enrolled", "visitor", "unrecognized"}:
                resolved_type = "unrecognized"
            user_id = _create_identity_user(
                name=name,
                sr_code=sr_code,
                gender=gender,
                program=program,
                user_type=resolved_type,
            )
            admitted_event_id = str(payload.get("admitted_event_id") or "").strip() or f"manual-{uuid.uuid4().hex}"
            _record_manual_entry_event(
                user_id=user_id,
                sr_code=sr_code,
                event_id=admitted_event_id,
                captured_at=captured_at,
                method="manual-resolution",
                resolution_action=action,
                metadata={
                    "source_event_id": source_event_id,
                    "performed_by": performer,
                    "notes": notes or None,
                },
            )
            bump_profiles_version(deps["db_path"])
            return jsonify(
                {
                    "success": True,
                    "action": action,
                    "event_id": source_event_id,
                    "admitted_event_id": admitted_event_id,
                    "user_id": int(user_id),
                    "user_type": resolved_type,
                }
            )

        if action == "visitor_entry":
            name = str(payload.get("name") or "").strip()
            if not name:
                return jsonify({"success": False, "message": "`name` is required for visitor entry."}), 400
            sr_code = str(payload.get("sr_code") or "").strip() or None
            gender = str(payload.get("gender") or "").strip() or ""
            program = str(payload.get("program") or "").strip() or ""
            user_id = None
            if sr_code:
                existing = deps["repository"].get_user_by_sr_code(sr_code)
                if existing is not None:
                    user_id = int(existing.id)
            if user_id is None:
                user_id = _create_identity_user(
                    name=name,
                    sr_code=sr_code,
                    gender=gender,
                    program=program,
                    user_type="visitor",
                )
            presence = _visitor_presence_state(int(user_id))
            if presence["inside_now"]:
                return jsonify(
                    {
                        "success": False,
                        "message": "Visitor is already marked inside. Record an exit before admitting again.",
                        **presence,
                    }
                ), 409
            entry_event_id = source_event_id or f"visitor-{uuid.uuid4().hex}"
            _record_manual_entry_event(
                user_id=user_id,
                sr_code=sr_code,
                event_id=entry_event_id,
                captured_at=captured_at,
                method="manual-resolution",
                resolution_action=action,
                metadata={"performed_by": performer, "notes": notes or None},
            )
            bump_profiles_version(deps["db_path"])
            return jsonify(
                {
                    "success": True,
                    "action": action,
                    "user_id": int(user_id),
                    "event_id": entry_event_id,
                    "user_type": "visitor",
                }
            )

        raw_user_id = payload.get("user_id")
        sr_code = str(payload.get("sr_code") or "").strip() or None
        if raw_user_id is None and not sr_code:
            return jsonify({"success": False, "message": "`user_id` or `sr_code` is required for visitor exit."}), 400
        user_id = None
        if raw_user_id is not None and str(raw_user_id).strip() != "":
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                return jsonify({"success": False, "message": "`user_id` must be an integer."}), 400
        elif sr_code:
            existing = deps["repository"].get_user_by_sr_code(sr_code)
            if existing:
                user_id = int(existing.id)
        if not user_id:
            return jsonify({"success": False, "message": "Visitor not found."}), 404

        presence = _visitor_presence_state(int(user_id))
        if not presence["inside_now"]:
            return jsonify(
                {
                    "success": False,
                    "message": "Visitor is not currently marked inside.",
                    **presence,
                }
            ), 409
        exit_event_id = source_event_id or f"visitor-exit-{uuid.uuid4().hex}"
        _record_manual_exit_event(
            user_id=user_id,
            sr_code=sr_code,
            event_id=exit_event_id,
            captured_at=captured_at,
            method="manual-resolution",
            resolution_action=action,
            metadata={"performed_by": performer, "notes": notes or None},
        )
        return jsonify(
            {
                "success": True,
                "action": action,
                "event_id": exit_event_id,
                "user_id": int(user_id),
                "status": "exit_recorded",
            }
        )
    
    @bp.route("/entry-logs", endpoint="entry_logs")
    @bp.route("/entry-exit-logs", endpoint="entry_exit_logs")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def entry_logs():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
 
        # All logs latest first
        c.execute("""
            SELECT u.name, u.sr_code, re.confidence, re.captured_at
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
            ORDER BY re.captured_at DESC
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
        from datetime import date, datetime

        college_code_map = {
            "College of Engineering": "COE",
            "College of Architecture, Fine Arts and Design": "CAFAD",
            "College of Arts and Sciences": "CAS",
            "College of Accountancy, Business, Economics, and International Hospitality Management": "CABEIHM",
            "College of Informatics and Computing Sciences": "CICS",
            "College of Nursing and Allied Health Sciences": "CNAHS",
            "College of Engineering Technology": "CET",
            "College of Agriculture and Forestry": "CAF",
            "College of Teacher Education": "CTE",
            OTHER_COLLEGE_LABEL: "OTHER",
        }

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
            SELECT
                re.user_id,
                u.name,
                u.sr_code,
                u.gender,
                u.course,
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') AS event_type,
                re.confidence,
                COALESCE(re.captured_at, re.ingested_at) AS event_time,
                COALESCE(re.payload_json, '') AS payload_json
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
        """
        params = []
        if selected_date:
            query += " WHERE DATE(COALESCE(re.captured_at, re.ingested_at)) = %s"
            params.append(selected_date)
        query += " ORDER BY COALESCE(re.captured_at, re.ingested_at) ASC, re.id ASC"

        c.execute(query, params)
        logs = c.fetchall()
        conn.close()

        catalog_entries = []
        program_to_college = {}
        for college_name, program_names in DEFAULT_COLLEGE_PROGRAM_MAP.items():
            for program_name in program_names:
                canonical_program = normalize_program_name(program_name)
                if not canonical_program:
                    continue
                catalog_entries.append((canonical_program, program_code_for(canonical_program)))
                program_to_college[canonical_program] = college_name
        program_lookup = build_program_lookup(catalog_entries)

        export_rows = []
        open_sessions_by_user = {}

        for (
            user_id,
            name,
            sr_code,
            gender,
            program,
            user_type,
            event_type,
            _confidence,
            event_time,
            payload_json,
        ) in logs:
            payload = _parse_payload_json_object(payload_json)
            snapshot_user_type = str(payload.get("identity_user_type") or payload.get("user_type") or "").strip()
            normalized_user_type = _normalize_user_type(snapshot_user_type or user_type)

            snapshot_name = payload.get("identity_name") or payload.get("user_name") or payload.get("name")
            snapshot_sr_code = payload.get("identity_sr_code") or payload.get("user_sr_code") or payload.get("sr_code")
            snapshot_gender = payload.get("identity_gender") or payload.get("gender")
            snapshot_program = payload.get("identity_program") or payload.get("program")

            display_name = _display_profile_field(name or snapshot_name, user_type=normalized_user_type)
            display_sr_code = _display_profile_field(sr_code or snapshot_sr_code, user_type=normalized_user_type)
            display_gender = _display_profile_field(
                gender or snapshot_gender,
                user_type=normalized_user_type,
                visitor_default="-",
            )

            raw_program = normalize_program_name(program or snapshot_program)
            if raw_program:
                resolved_program, resolution_status, _ = resolve_program_name(raw_program, program_lookup)
                canonical_program = normalize_program_name(resolved_program)
                if resolution_status in {"catalog", "registered"} and canonical_program:
                    resolved_college = program_to_college.get(canonical_program, OTHER_COLLEGE_LABEL)
                    display_program = normalize_program_name(program_code_for(canonical_program)) or canonical_program
                    display_college = college_code_map.get(resolved_college, "OTHER")
                else:
                    display_program = raw_program.upper()
                    display_college = "OTHER"
            elif normalized_user_type == "visitor":
                display_program = "VIS"
                display_college = "VIS"
            elif normalized_user_type == "staff":
                display_program = "STAFF"
                display_college = "STAFF"
            else:
                display_program = "N/A"
                display_college = "OTHER"

            normalized_event_type = str(event_type or "").strip().lower() or "entry"
            if normalized_event_type not in {"entry", "exit"}:
                normalized_event_type = "unknown"

            timestamp_text = _normalize_timestamp_for_json(event_time)
            date_text = timestamp_text[:10] if timestamp_text else ""
            time_text = timestamp_text[11:19] if len(timestamp_text) >= 19 else timestamp_text
            user_key = (
                str(date_text or "").strip(),
                int(user_id) if user_id is not None else None,
                str(display_sr_code or "").strip().upper(),
                str(display_name or "").strip().upper(),
            )

            if normalized_event_type == "entry":
                row_data = {
                    "date": date_text,
                    "sr_code": display_sr_code,
                    "name": display_name,
                    "sex": display_gender,
                    "college_office": display_college,
                    "program": display_program,
                    "entry_timestamp": time_text,
                    "exit_timestamp": "",
                }
                open_sessions_by_user.setdefault(user_key, []).append(row_data)
                export_rows.append(row_data)
            elif normalized_event_type == "exit":
                open_sessions = open_sessions_by_user.get(user_key) or []
                if open_sessions:
                    open_sessions[-1]["exit_timestamp"] = time_text
                    open_sessions.pop()
                    if not open_sessions:
                        open_sessions_by_user.pop(user_key, None)
                else:
                    export_rows.append(
                        {
                            "date": date_text,
                            "sr_code": display_sr_code,
                            "name": display_name,
                            "sex": display_gender,
                            "college_office": display_college,
                            "program": display_program,
                            "entry_timestamp": "",
                            "exit_timestamp": time_text,
                        }
                    )

        response = _build_entry_exit_logs_workbook(
            export_rows,
            selected_date=selected_date,
            filename_date=date_for_name.strftime("%m-%d-%Y"),
        )
        log_action("EXPORT_LOGS")
        return response

    @bp.route("/program-monthly-visits/export", endpoint="export_program_monthly_visits")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def export_program_monthly_visits():
        try:
            payload = _monthly_program_visits_data(request.args.get("year", "").strip())
            response = _build_program_monthly_visits_workbook(payload)
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 500
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        log_action("EXPORT_PROGRAM_MONTHLY_VISITS", target=str(payload["year"]))
        return response

    @bp.route("/monthly-daily-visits/export", endpoint="export_monthly_daily_visits")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def export_monthly_daily_visits():
        try:
            payload = _monthly_daily_visits_data(
                request.args.get("year", "").strip(),
                request.args.get("month", "").strip(),
            )
            response = _build_monthly_daily_visits_workbook(payload)
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 500
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        log_action(
            "EXPORT_MONTHLY_DAILY_VISITS",
            target=f"{payload['year']}-{payload['month']:02d}",
        )
        return response

    @bp.route("/api/dashboard", methods=["GET"], endpoint="api_dashboard")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_dashboard():
        return jsonify(_dashboard_data(request.args.get("filter")))

    @bp.route("/api/settings/recognition", methods=["GET", "POST"], endpoint="api_settings")
    @bp.route("/api/settings", methods=["GET", "POST"])
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_settings():
        role = str(session.get("role") or "").strip().lower()
        if request.method == "POST":
            if role == "library_staff":
                return jsonify({"success": False, "message": "Forbidden."}), 403

            payload = request.get_json(silent=True) or {}
            if role == "library_admin":
                forbidden_fields = [
                    key
                    for key in (
                        "threshold",
                        "primary_threshold",
                        "secondary_threshold",
                        "quality_threshold",
                        "face_quality_profiles",
                        "recognition_confidence_threshold",
                        "online_learning_confidence_threshold",
                        "recognition_event_retention_days",
                        "entry_cctv_stream_source",
                        "exit_cctv_stream_source",
                    )
                    if key in payload
                ]
                if forbidden_fields:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "message": "Library administrators cannot modify recognition threshold settings.",
                            }
                        ),
                        403,
                    )

            current_settings = _read_recognition_settings()
            next_settings = dict(current_settings)
            changed_fields = {}

            if role == "super_admin":
                threshold_bounds = SETTINGS_BOUNDS["threshold"]
                threshold_value, threshold_error = _parse_bounded_float_payload(
                    payload,
                    "threshold",
                    float(threshold_bounds["min"]),
                    float(threshold_bounds["max"]),
                )
                if threshold_error:
                    return jsonify({"success": False, "message": threshold_error}), 400
                if threshold_value is not None:
                    next_settings["threshold"] = threshold_value
                    if threshold_value != current_settings["threshold"]:
                        changed_fields["threshold"] = (current_settings["threshold"], threshold_value)

                primary_bounds = SETTINGS_BOUNDS["primary_threshold"]
                primary_threshold_value, primary_threshold_error = _parse_bounded_float_payload(
                    payload,
                    "primary_threshold",
                    float(primary_bounds["min"]),
                    float(primary_bounds["max"]),
                )
                if primary_threshold_error:
                    return jsonify({"success": False, "message": primary_threshold_error}), 400
                if primary_threshold_value is not None:
                    next_settings["primary_threshold"] = primary_threshold_value
                    if primary_threshold_value != current_settings["primary_threshold"]:
                        changed_fields["primary_threshold"] = (
                            current_settings["primary_threshold"],
                            primary_threshold_value,
                        )

                secondary_bounds = SETTINGS_BOUNDS["secondary_threshold"]
                secondary_threshold_value, secondary_threshold_error = _parse_bounded_float_payload(
                    payload,
                    "secondary_threshold",
                    float(secondary_bounds["min"]),
                    float(secondary_bounds["max"]),
                )
                if secondary_threshold_error:
                    return jsonify({"success": False, "message": secondary_threshold_error}), 400
                if secondary_threshold_value is not None:
                    next_settings["secondary_threshold"] = secondary_threshold_value
                    if secondary_threshold_value != current_settings["secondary_threshold"]:
                        changed_fields["secondary_threshold"] = (
                            current_settings["secondary_threshold"],
                            secondary_threshold_value,
                        )

                quality_bounds = SETTINGS_BOUNDS["quality_threshold"]
                quality_threshold_value, quality_error = _parse_bounded_float_payload(
                    payload,
                    "quality_threshold",
                    float(quality_bounds["min"]),
                    float(quality_bounds["max"]),
                )
                if quality_error:
                    return jsonify({"success": False, "message": quality_error}), 400
                if quality_threshold_value is not None:
                    next_settings["quality_threshold"] = quality_threshold_value
                    if quality_threshold_value != current_settings["quality_threshold"]:
                        changed_fields["quality_threshold"] = (
                            current_settings["quality_threshold"],
                            quality_threshold_value,
                        )

                confidence_bounds = SETTINGS_BOUNDS["recognition_confidence_threshold"]
                recognition_confidence_value, confidence_error = _parse_bounded_float_payload(
                    payload,
                    "recognition_confidence_threshold",
                    float(confidence_bounds["min"]),
                    float(confidence_bounds["max"]),
                )
                if confidence_error:
                    return jsonify({"success": False, "message": confidence_error}), 400
                if recognition_confidence_value is not None:
                    next_settings["recognition_confidence_threshold"] = recognition_confidence_value
                    if recognition_confidence_value != current_settings["recognition_confidence_threshold"]:
                        changed_fields["recognition_confidence_threshold"] = (
                            current_settings["recognition_confidence_threshold"],
                            recognition_confidence_value,
                        )

                learning_bounds = SETTINGS_BOUNDS["online_learning_confidence_threshold"]
                online_learning_confidence_value, learning_error = _parse_bounded_float_payload(
                    payload,
                    "online_learning_confidence_threshold",
                    float(learning_bounds["min"]),
                    float(learning_bounds["max"]),
                )
                if learning_error:
                    return jsonify({"success": False, "message": learning_error}), 400
                if online_learning_confidence_value is not None:
                    next_settings["online_learning_confidence_threshold"] = online_learning_confidence_value
                    if online_learning_confidence_value != current_settings["online_learning_confidence_threshold"]:
                        changed_fields["online_learning_confidence_threshold"] = (
                            current_settings["online_learning_confidence_threshold"],
                            online_learning_confidence_value,
                        )

                profile_updates, profile_error = _parse_quality_profiles_payload(payload)
                if profile_error:
                    return jsonify({"success": False, "message": profile_error}), 400
                if profile_updates is not None:
                    next_profiles = {
                        context: dict(current_settings["face_quality_profiles"].get(context, {}))
                        for context in QUALITY_CONTEXTS
                    }
                    for context, profile_payload in profile_updates.items():
                        for field_name, parsed_value in profile_payload.items():
                            previous_value = next_profiles[context].get(field_name)
                            next_profiles[context][field_name] = parsed_value
                            if parsed_value != previous_value:
                                changed_fields[_quality_setting_key(context, field_name)] = (
                                    previous_value,
                                    parsed_value,
                                )
                    next_settings["face_quality_profiles"] = next_profiles

            vector_bounds = SETTINGS_BOUNDS["vector_index_top_k"]
            vector_value, vector_error = _parse_bounded_int_payload(
                payload,
                "vector_index_top_k",
                int(vector_bounds["min"]),
                int(vector_bounds["max"]),
            )
            if vector_error:
                return jsonify({"success": False, "message": vector_error}), 400
            if vector_value is not None:
                next_settings["vector_index_top_k"] = vector_value
                if vector_value != current_settings["vector_index_top_k"]:
                    changed_fields["vector_index_top_k"] = (current_settings["vector_index_top_k"], vector_value)

            occupancy_bounds = SETTINGS_BOUNDS["max_occupancy"]
            max_occupancy_value, max_occupancy_error = _parse_bounded_int_payload(
                payload,
                "max_occupancy",
                int(occupancy_bounds["min"]),
                int(occupancy_bounds["max"]),
            )
            if max_occupancy_error:
                return jsonify({"success": False, "message": max_occupancy_error}), 400
            if max_occupancy_value is not None:
                next_settings["max_occupancy"] = max_occupancy_value
                if max_occupancy_value != current_settings["max_occupancy"]:
                    changed_fields["max_occupancy"] = (current_settings["max_occupancy"], max_occupancy_value)

            warning_bounds = SETTINGS_BOUNDS["occupancy_warning_threshold"]
            warning_value, warning_error = _parse_bounded_float_payload(
                payload,
                "occupancy_warning_threshold",
                float(warning_bounds["min"]),
                float(warning_bounds["max"]),
            )
            if warning_error:
                return jsonify({"success": False, "message": warning_error}), 400
            if warning_value is not None:
                next_settings["occupancy_warning_threshold"] = warning_value
                if warning_value != current_settings["occupancy_warning_threshold"]:
                    changed_fields["occupancy_warning_threshold"] = (
                        current_settings["occupancy_warning_threshold"],
                        warning_value,
                    )

            interval_bounds = SETTINGS_BOUNDS["occupancy_snapshot_interval_seconds"]
            interval_value, interval_error = _parse_bounded_int_payload(
                payload,
                "occupancy_snapshot_interval_seconds",
                int(interval_bounds["min"]),
                int(interval_bounds["max"]),
            )
            if interval_error:
                return jsonify({"success": False, "message": interval_error}), 400
            if interval_value is not None:
                next_settings["occupancy_snapshot_interval_seconds"] = interval_value
                if interval_value != current_settings["occupancy_snapshot_interval_seconds"]:
                    changed_fields["occupancy_snapshot_interval_seconds"] = (
                        current_settings["occupancy_snapshot_interval_seconds"],
                        interval_value,
                    )

            if role == "super_admin":
                event_retention_bounds = SETTINGS_BOUNDS["recognition_event_retention_days"]
                event_retention_value, event_retention_error = _parse_bounded_int_payload(
                    payload,
                    "recognition_event_retention_days",
                    int(event_retention_bounds["min"]),
                    int(event_retention_bounds["max"]),
                )
                if event_retention_error:
                    return jsonify({"success": False, "message": event_retention_error}), 400
                if event_retention_value is not None:
                    next_settings["recognition_event_retention_days"] = event_retention_value
                    if event_retention_value != current_settings["recognition_event_retention_days"]:
                        changed_fields["recognition_event_retention_days"] = (
                            current_settings["recognition_event_retention_days"],
                            event_retention_value,
                        )

                entry_source_value, entry_source_error = _parse_text_payload(
                    payload,
                    "entry_cctv_stream_source",
                )
                if entry_source_error:
                    return jsonify({"success": False, "message": entry_source_error}), 400
                if entry_source_value is not None:
                    next_settings["entry_cctv_stream_source"] = entry_source_value
                    if entry_source_value != current_settings["entry_cctv_stream_source"]:
                        changed_fields["entry_cctv_stream_source"] = (
                            current_settings["entry_cctv_stream_source"],
                            entry_source_value,
                        )

                exit_source_value, exit_source_error = _parse_text_payload(
                    payload,
                    "exit_cctv_stream_source",
                )
                if exit_source_error:
                    return jsonify({"success": False, "message": exit_source_error}), 400
                if exit_source_value is not None:
                    next_settings["exit_cctv_stream_source"] = exit_source_value
                    if exit_source_value != current_settings["exit_cctv_stream_source"]:
                        changed_fields["exit_cctv_stream_source"] = (
                            current_settings["exit_cctv_stream_source"],
                            exit_source_value,
                        )

            if changed_fields:
                for setting_key in changed_fields:
                    if setting_key in next_settings:
                        _set_setting(deps["db_path"], setting_key, next_settings[setting_key])
                    elif "_quality_" in setting_key:
                        context, field_name = setting_key.split("_quality_", 1)
                        _set_setting(
                            deps["db_path"],
                            setting_key,
                            next_settings["face_quality_profiles"][context][field_name],
                        )
                deps["set_thresholds"](
                    float(next_settings["threshold"]),
                    float(next_settings["quality_threshold"]),
                )
                deps["config"].apply_quality_profiles(next_settings.get("face_quality_profiles"))
                deps["config"].primary_threshold = float(next_settings["primary_threshold"])
                deps["config"].secondary_threshold = float(next_settings["secondary_threshold"])
                deps["config"].vector_index_top_k = int(next_settings["vector_index_top_k"])
                deps["config"].recognition_confidence_threshold = float(
                    next_settings["recognition_confidence_threshold"]
                )
                deps["config"].online_learning_confidence_threshold = float(
                    next_settings["online_learning_confidence_threshold"]
                )
                deps["config"].max_library_capacity = int(next_settings["max_occupancy"])
                deps["config"].occupancy_warning_threshold = float(next_settings["occupancy_warning_threshold"])
                deps["config"].occupancy_snapshot_interval_seconds = int(
                    next_settings["occupancy_snapshot_interval_seconds"]
                )
                deps["config"].recognition_event_retention_days = int(
                    next_settings["recognition_event_retention_days"]
                )
                deps["config"].entry_cctv_stream_source = str(next_settings["entry_cctv_stream_source"])
                deps["config"].exit_cctv_stream_source = str(next_settings["exit_cctv_stream_source"])
                bump_settings_version(deps["db_path"])

                ordered_keys = [
                    "threshold",
                    "primary_threshold",
                    "secondary_threshold",
                    "quality_threshold",
                    "recognition_confidence_threshold",
                    "online_learning_confidence_threshold",
                    "vector_index_top_k",
                    "max_occupancy",
                    "occupancy_warning_threshold",
                    "occupancy_snapshot_interval_seconds",
                    "recognition_event_retention_days",
                    "entry_cctv_stream_source",
                    "exit_cctv_stream_source",
                ]
                summary_parts = []
                for setting_key in ordered_keys:
                    if setting_key not in changed_fields:
                        continue
                    previous_value, updated_value = changed_fields[setting_key]
                    summary_parts.append(
                        f"{setting_key}: {_format_setting_value(setting_key, previous_value)} -> "
                        f"{_format_setting_value(setting_key, updated_value)}"
                    )
                for setting_key in sorted(k for k in changed_fields if "_quality_" in k):
                    previous_value, updated_value = changed_fields[setting_key]
                    summary_parts.append(
                        f"{setting_key}: {_format_setting_value(setting_key, previous_value)} -> "
                        f"{_format_setting_value(setting_key, updated_value)}"
                    )
                if summary_parts:
                    log_action(SETTINGS_AUDIT_ACTION, target="; ".join(summary_parts))

        return jsonify(_build_settings_payload(role))

    _PROFILE_SORT_SQL = {
        "name_asc": "name ASC, user_id ASC",
        "name_desc": "name DESC, user_id DESC",
        "created_desc": "created_at DESC, user_id DESC",
        "created_asc": "created_at ASC, user_id ASC",
        "updated_desc": "last_updated DESC, user_id DESC",
        "updated_asc": "last_updated ASC, user_id ASC",
        "archived_desc": "archived_at DESC, user_id DESC",
        "archived_asc": "archived_at ASC, user_id ASC",
    }

    def _parse_positive_query_int(name: str, default: int, min_value: int = 1, max_value: int = 200) -> int:
        raw_value = request.args.get(name, default)
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(min_value, min(max_value, parsed))

    def _normalize_profile_status(value: str | None) -> str:
        normalized = str(value or "active").strip().lower()
        return normalized if normalized in {"active", "archived"} else "active"

    @bp.route("/api/profiles", methods=["GET"], endpoint="api_profiles_list")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_profiles_list():
        status = _normalize_profile_status(request.args.get("status"))
        query = (request.args.get("q") or "").strip().lower()
        program = (request.args.get("program") or "").strip()
        page_size = _parse_positive_query_int("page_size", default=10, min_value=1, max_value=200)
        page = _parse_positive_query_int("page", default=1, min_value=1, max_value=1000000)

        default_sort = "created_desc" if status == "active" else "archived_desc"
        requested_sort = str(request.args.get("sort") or default_sort).strip().lower()
        sort_key = requested_sort if requested_sort in _PROFILE_SORT_SQL else default_sort
        order_by_sql = _PROFILE_SORT_SQL[sort_key]

        status_condition = "archived_at IS NULL" if status == "active" else "archived_at IS NOT NULL"
        where_clauses = [status_condition]
        where_params: list[object] = []

        if query:
            where_clauses.append("(LOWER(COALESCE(name, '')) LIKE %s OR LOWER(COALESCE(sr_code, '')) LIKE %s)")
            like_query = f"%{query}%"
            where_params.extend([like_query, like_query])

        if program:
            where_clauses.append("course = %s")
            where_params.append(program)

        where_sql = " AND ".join(where_clauses)

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        c.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END), 0) AS active_count,
                COALESCE(SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS archived_count
            FROM users
            """
        )
        counts_row = c.fetchone() or (0, 0)
        counts = {
            "active": int(counts_row[0] or 0),
            "archived": int(counts_row[1] or 0),
        }

        c.execute(f"SELECT COUNT(*) FROM users WHERE {where_sql}", tuple(where_params))
        total = int((c.fetchone() or [0])[0] or 0)
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size

        c.execute(
            f"""
            SELECT user_id, name, sr_code, gender, course AS program, created_at, last_updated, archived_at, user_type
            FROM users
            WHERE {where_sql}
            ORDER BY {order_by_sql}
            LIMIT %s OFFSET %s
            """,
            tuple([*where_params, page_size, offset]),
        )
        rows = [
            {
                "user_id": row[0],
                "name": _display_profile_field(row[1], user_type=row[8]),
                "sr_code": _display_profile_field(row[2], user_type=row[8]),
                "gender": _display_profile_field(row[3], user_type=row[8]),
                "program": _display_profile_field(row[4], user_type=row[8]),
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
                "archived_at": _normalize_timestamp_for_json(row[7], "-"),
                "user_type": _normalize_user_type(row[8]),
            }
            for row in c.fetchall()
        ]

        c.execute(
            f"""
            SELECT DISTINCT course
            FROM users
            WHERE {status_condition} AND course IS NOT NULL AND TRIM(course) <> ''
            ORDER BY course ASC
            """
        )
        programs = [str(row[0]).strip() for row in c.fetchall() if str(row[0] or "").strip()]
        conn.close()

        return jsonify(
            {
                "rows": rows,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "counts": counts,
                "programs": programs,
                "status": status,
                "sort": sort_key,
            }
        )

    @bp.route("/api/registered-profiles", methods=["GET"], endpoint="api_registered_profiles")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/profiles", methods=["POST"], endpoint="api_profiles_create")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_profiles_create():
        payload = request.get_json(silent=True) or {}
        name = _normalize_person_name(payload.get("name"))
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
    @role_required("super_admin", "library_admin", "library_staff")
    def api_profiles_update(user_id):
        payload = request.get_json(silent=True) or {}
        name = _normalize_person_name(payload.get("name"))
        sr_code = (payload.get("sr_code") or "").strip()
        gender = (payload.get("gender") or "").strip()
        program = (payload.get("program") or "").strip()

        is_valid, validation_message, invalid_field = _validate_registration_fields(name, sr_code, gender, program)
        if not is_valid:
            return jsonify({"success": False, "message": validation_message, "field": invalid_field}), 400

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
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
            SET name = %s, sr_code = %s, gender = %s, course = %s, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = %s
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

    @bp.route("/api/profiles/<int:user_id>", methods=["DELETE"], endpoint="api_profiles_delete")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_profiles_delete(user_id):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            SELECT name, sr_code, archived_at, gender, course, user_type
            FROM users
            WHERE user_id = %s
            """,
            (user_id,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Profile not found."}), 404

        name = row[0] or "-"
        sr_code = row[1] or "-"
        archived_at = row[2]
        gender = row[3] or ""
        program = row[4] or ""
        user_type = row[5] or "unrecognized"
        if archived_at is None or not str(archived_at).strip():
            conn.close()
            return jsonify({"success": False, "message": "Active profiles must be archived before deletion."}), 409

        try:
            _snapshot_recognition_event_identity(
                c,
                user_id=int(user_id),
                name=name,
                sr_code=sr_code,
                gender=gender,
                program=program,
                user_type=user_type,
            )
            c.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            return jsonify({"success": False, "message": f"Failed to delete profile: {exc}"}), 400

        conn.close()
        bump_profiles_version(deps["db_path"])
        deps["remove_user_embedding"](int(user_id))
        log_action("DELETE_PROFILE_RECORD", target=f"{name} ({sr_code})")
        return jsonify({"success": True, "user_id": int(user_id)})

    @bp.route("/api/archive-profiles", methods=["GET"], endpoint="api_archive_profiles")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/archive-profiles/submit", methods=["POST"], endpoint="api_archive_profiles_submit")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_archive_profiles_submit():
        payload = request.get_json(silent=True) or {}
        user_ids = payload.get("user_ids") or []
        if not user_ids:
            return jsonify({"success": False, "message": "No profiles selected for archiving."}), 400
        normalized_user_ids = []
        for raw_user_id in user_ids:
            try:
                normalized_user_ids.append(int(raw_user_id))
            except (TypeError, ValueError):
                continue
        normalized_user_ids = sorted(set(normalized_user_ids))
        if not normalized_user_ids:
            return jsonify({"success": False, "message": "No valid profile IDs were provided."}), 400

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            f"""
            SELECT user_id, name, sr_code, gender, course, user_type
            FROM users
            WHERE user_id IN ({",".join("%s" for _ in normalized_user_ids)})
            """,
            tuple(normalized_user_ids),
        )
        for row in c.fetchall():
            _snapshot_recognition_event_identity(
                c,
                user_id=int(row[0]),
                name=row[1],
                sr_code=row[2],
                gender=row[3],
                program=row[4],
                user_type=row[5],
            )
        c.execute(
            f"""
            UPDATE users
            SET archived_at = CURRENT_TIMESTAMP
            WHERE user_id IN ({",".join("%s" for _ in normalized_user_ids)})
            """,
            tuple(normalized_user_ids),
        )
        conn.commit()
        conn.close()

        bump_profiles_version(deps["db_path"])
        log_action("ARCHIVE_PROFILES", target=",".join(map(str, normalized_user_ids)))
        return jsonify({"success": True})

    @bp.route("/api/archived-profiles", methods=["GET"], endpoint="api_archived_profiles")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
                "archived_at": _normalize_timestamp_for_json(row[7], "-"),
            }
            for row in c.fetchall()
        ]
        conn.close()
        return jsonify({"rows": rows})

    @bp.route("/api/archived-profiles/restore", methods=["POST"], endpoint="api_archived_profiles_restore")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
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
            WHERE user_id IN ({",".join("%s" for _ in user_ids)})
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
                    parsed_timestamp = datetime.strptime(raw_timestamp, fmt).replace(tzinfo=timezone.utc)
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
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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
                "earliest": _normalize_date_key(row[2]) or "",
                "latest": _normalize_date_key(row[3]) or "",
                "imported_at": _normalize_timestamp_for_json(row[4]),
            }
            for row in c.fetchall()
        ]

        c.execute("SELECT COUNT(*) FROM recognition_events")
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
        c.execute("DELETE FROM imported_logs WHERE import_batch = %s", (batch_id,))
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

    @bp.route("/api/analytics/daily-report", methods=["GET"], endpoint="api_analytics_daily_report")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_analytics_daily_report():
        date_param = (request.args.get("date") or "").strip()
        target_date = None
        if date_param:
            try:
                target_date = datetime.fromisoformat(date_param).date()
            except ValueError:
                return jsonify(
                    {
                        "success": False,
                        "message": "Invalid `date` format. Use YYYY-MM-DD.",
                    }
                ), 400

        service = OccupancyService(deps["db_path"])
        return jsonify(service.get_daily_report(target_date))

    @bp.route("/api/analytics/occupancy-trends", methods=["GET"], endpoint="api_analytics_occupancy_trends")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_analytics_occupancy_trends():
        days_param = (request.args.get("days") or "7").strip()
        try:
            days = int(days_param)
        except ValueError:
            return jsonify(
                {
                    "success": False,
                    "message": "`days` must be an integer between 1 and 365.",
                }
            ), 400
        if days < 1 or days > 365:
            return jsonify(
                {
                    "success": False,
                    "message": "`days` must be an integer between 1 and 365.",
                }
            ), 400

        service = OccupancyService(deps["db_path"])
        return jsonify(service.get_occupancy_trends(days=days))

    @bp.route("/api/events", methods=["GET"], endpoint="api_events")
    @bp.route("/api/entry-logs", methods=["GET"])
    @bp.route("/api/entry-exit-logs", methods=["GET"])
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_events():
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        raw_logs = []
        source_mode = ""

        start_date = (request.args.get("start_date") or "").strip()
        end_date = (request.args.get("end_date") or "").strip()
        event_type_filter = (request.args.get("type") or "").strip().lower()
        user_type_filter = (request.args.get("user_type") or "").strip().lower()
        if event_type_filter not in {"", "entry", "exit", "unrecognized"}:
            event_type_filter = ""

        for value, label in ((start_date, "start_date"), (end_date, "end_date")):
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value).date().isoformat()
                if label == "start_date":
                    start_date = parsed
                else:
                    end_date = parsed
            except ValueError:
                conn.close()
                return jsonify(
                    {
                        "success": False,
                        "message": f"Invalid `{label}` format. Use YYYY-MM-DD.",
                    }
                ), 400

        try:
            c.execute(
                """
                SELECT
                    e.id,
                    COALESCE(e.event_id, '') AS event_id,
                    e.user_id,
                    COALESCE(u.name, '') AS name,
                    COALESCE(e.sr_code, u.sr_code, '') AS sr_code,
                    COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                    COALESCE(e.confidence, 0.0) AS confidence,
                    COALESCE(NULLIF(TRIM(e.event_type), ''), 'entry') AS event_type,
                    COALESCE(e.captured_at, e.ingested_at) AS event_time,
                    COALESCE(e.decision, 'allowed') AS decision,
                    COALESCE(e.payload_json, '') AS payload_json
                FROM recognition_events e
                LEFT JOIN users u ON e.user_id = u.user_id
                ORDER BY e.ingested_at DESC, e.id DESC
                LIMIT 1000
                """
            )
            raw_logs = c.fetchall()
            source_mode = "enhanced"
        except Exception:
            raw_logs = []
            source_mode = ""
        if not raw_logs:
            try:
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
                    LIMIT 1000
                    """
                )
                raw_logs = c.fetchall()
                source_mode = "events_fallback"
            except Exception:
                raw_logs = []
                source_mode = ""

        rows = []
        for raw in raw_logs:
            if source_mode == "enhanced" and len(raw) >= 11:
                (
                    event_row_id,
                    event_id,
                    user_id,
                    name,
                    sr_code,
                    user_type,
                    confidence,
                    event_type,
                    event_time,
                    decision,
                    payload_json,
                ) = raw
                payload = _parse_payload_json_object(payload_json)
                event_type = str(event_type or "").strip().lower() or "entry"
                if event_type not in {"entry", "exit"}:
                    event_type = "unknown"
                snapshot_user_type = str(payload.get("identity_user_type") or payload.get("user_type") or "").strip()
                normalized_user_type = _normalize_user_type(snapshot_user_type or user_type)
                snapshot_name = (
                    payload.get("identity_name")
                    or payload.get("user_name")
                    or payload.get("name")
                )
                snapshot_sr_code = (
                    payload.get("identity_sr_code")
                    or payload.get("user_sr_code")
                    or payload.get("sr_code")
                )
                name = _display_profile_field(name or snapshot_name, user_type=normalized_user_type)
                sr_code = _display_profile_field(sr_code or snapshot_sr_code, user_type=normalized_user_type)
                normalized_decision = str(decision or "allowed").strip().lower() or "allowed"
            else:
                if len(raw) == 4:
                    name, sr_code, confidence, event_time = raw
                else:
                    name, sr_code, confidence, event_time = raw[0], raw[1], raw[2], raw[3]
                event_row_id = None
                event_id = ""
                user_id = None
                event_type = "entry"
                normalized_user_type = "enrolled"
                normalized_decision = "allowed"
                name = _display_profile_field(name, user_type=normalized_user_type)
                sr_code = _display_profile_field(sr_code, user_type=normalized_user_type)

            value = _coerce_confidence(confidence) or 0.0
            conf_pct = int(value * 100.0)
            if isinstance(event_time, datetime):
                timestamp_text = event_time.isoformat(sep=" ", timespec="seconds")
            elif event_time:
                timestamp_text = str(event_time)
            else:
                timestamp_text = ""
            date_value = timestamp_text[:10] if timestamp_text else ""
            if (start_date or end_date) and not date_value:
                continue
            if start_date and date_value and date_value < start_date:
                continue
            if end_date and date_value and date_value > end_date:
                continue
            if event_type_filter == "entry" and event_type != "entry":
                continue
            if event_type_filter == "exit" and event_type != "exit":
                continue
            if event_type_filter == "unrecognized":
                if normalized_user_type != "unrecognized" and normalized_decision != "unknown":
                    continue
            if user_type_filter and normalized_user_type != user_type_filter:
                continue

            camera_id = 1 if event_type == "entry" else 2 if event_type == "exit" else None
            row_data = {
                "id": event_row_id,
                "event_id": event_id,
                "user_id": user_id,
                "name": name,
                "sr_code": sr_code,
                "user_type": normalized_user_type,
                "event_type": event_type,
                "camera_id": camera_id,
                "status": normalized_decision,
                "conf_pct": conf_pct,
                "timestamp": _normalize_timestamp_for_json(timestamp_text),
                "entered_at": _normalize_timestamp_for_json(timestamp_text) if event_type == "entry" else None,
                "exited_at": _normalize_timestamp_for_json(timestamp_text) if event_type == "exit" else None,
                "date": date_value,
                "time": timestamp_text[11:19] if len(timestamp_text) >= 19 else "",
            }
            rows.append(row_data)
        conn.close()
        events = [
            {
                "id": row.get("id"),
                "event_id": row.get("event_id"),
                "user_id": row.get("user_id"),
                "user_name": row.get("name"),
                "event_type": row.get("event_type"),
                "camera_id": row.get("camera_id"),
                "timestamp": row.get("timestamp"),
                "status": row.get("status"),
                "user_type": row.get("user_type"),
            }
            for row in rows
        ]
        return jsonify({"total": len(rows), "rows": rows, "events": events})

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
            LIMIT %s
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
                "timestamp": _normalize_timestamp_for_json(row[6]),
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

    @bp.route("/api/monthly-daily-visits", methods=["GET"], endpoint="api_monthly_daily_visits")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def api_monthly_daily_visits():
        payload = _monthly_daily_visits_data(
            request.args.get("year", "").strip(),
            request.args.get("month", "").strip(),
        )
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
            "/api/registrations/active",
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
