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
import json
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
from core.program_catalog import (
    build_program_lookup,
    is_program_code,
    normalize_program_name,
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

    def _insert_user_registration_audit(
        *,
        registration_type: str,
        flow_type: str,
        status: str,
        performed_by: str,
        user_id: int | None = None,
        event_id: str | None = None,
        notes: str | None = None,
    ) -> None:
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO user_registrations (
                user_id, event_id, registration_type, flow_type, status, performed_by, notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                event_id,
                registration_type,
                flow_type,
                status,
                performed_by,
                notes,
            ),
        )
        conn.commit()
        conn.close()

    def _create_identity_user(
        *,
        name: str,
        sr_code: str | None,
        gender: str | None,
        program: str | None,
        user_type: str,
        flow_type: str,
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
            flow_type,
        )
        c.execute(
            """
            INSERT INTO users (
                name, sr_code, gender, course, embeddings, image_paths, embedding_dim, user_type, flow_type
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING user_id
            """,
            params,
        )
        row = c.fetchone()
        user_id = int(row[0])
        conn.commit()
        conn.close()
        return user_id

    def _record_manual_entry_event(*, user_id: int | None, sr_code: str | None, event_id: str, captured_at: datetime):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        payload_json = {
            "event_id": event_id,
            "user_id": user_id,
            "sr_code": sr_code,
            "decision": "allowed",
            "source": "librarian_manual_admission",
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
                "manual-approval",
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

    def _record_manual_exit_event(*, user_id: int | None, sr_code: str | None, event_id: str, captured_at: datetime):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        payload_json = {
            "event_id": event_id,
            "user_id": user_id,
            "sr_code": sr_code,
            "decision": "allowed",
            "source": "librarian_manual_exit",
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
                "manual-exit",
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

    def _resolve_registration_status_reason(reg_state, stream_status, worker_attached, detection_paused):
        code = (getattr(reg_state, "status_reason_code", None) or "").strip() or None
        message = (getattr(reg_state, "status_reason_message", "") or "").strip()
        updated_at = (getattr(reg_state, "status_updated_at", None) or "").strip() or None

        stream_state = str((stream_status or {}).get("state") or "").strip().lower()
        stream_message = str((stream_status or {}).get("message") or "").strip()

        if not worker_attached:
            return (
                "worker_unattached",
                "Entry recognition worker is offline. Start the entry worker and wait for heartbeat sync.",
                _utc_now_iso(),
            )

        if detection_paused:
            return (
                "detection_paused",
                "Detection is paused while website registration uses the camera stream.",
                _utc_now_iso(),
            )

        if stream_state in {"disconnected", "reconnecting", "connecting"}:
            return (
                f"stream_{stream_state}",
                stream_message or "Camera stream is unavailable. Check camera source and reconnect.",
                _utc_now_iso(),
            )

        if getattr(reg_state, "session_expired", False):
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
        heartbeat_ttl_seconds = int(getattr(deps["config"], "registration_worker_heartbeat_ttl_seconds", 10) or 10)
        worker_online_checker = deps.get("is_worker_online")
        if callable(worker_online_checker):
            worker_attached = bool(worker_online_checker("entry", heartbeat_ttl_seconds))
        else:
            worker_attached = bool(deps.get("worker_runtime_attached"))
        worker_last_seen_getter = deps.get("get_worker_last_seen_at")
        entry_worker_last_seen_at = None
        if callable(worker_last_seen_getter):
            raw_last_seen = worker_last_seen_getter("entry")
            if isinstance(raw_last_seen, (int, float)):
                entry_worker_last_seen_at = float(raw_last_seen)
        detection_paused = bool(deps["detection_paused"]())
        session_timeout_seconds = int(getattr(deps["config"], "registration_session_timeout_seconds", 0) or 0)
        session_started_at = float(reg_state.session_started_at) if reg_state.session_started_at is not None else None
        last_activity_at = float(reg_state.last_activity_at) if reg_state.last_activity_at is not None else None
        session_expires_at = None
        seconds_until_expiry = None
        if session_timeout_seconds > 0 and last_activity_at is not None:
            session_expires_at = last_activity_at + session_timeout_seconds
            seconds_until_expiry = max(0, int(session_expires_at - time.time()))
        elif bool(getattr(reg_state, "session_expired", False)):
            seconds_until_expiry = 0
        reason_code, reason_message, reason_updated_at = _resolve_registration_status_reason(
            reg_state=reg_state,
            stream_status=stream_status,
            worker_attached=worker_attached,
            detection_paused=detection_paused,
        )
        return {
            "capture_count": captured_total,
            "max_captures": reg_state.max_captures,
            "has_pending_registration": bool(reg_state.pending_registration),
            "is_in_progress": reg_state.in_progress,
            "web_session_active": bool(reg_state.web_session_active),
            "allow_unknown_override": bool(getattr(reg_state, "allow_unknown_override", False)),
            "session_expired": bool(getattr(reg_state, "session_expired", False)),
            "worker_attached": worker_attached,
            "entry_worker_last_seen_at": entry_worker_last_seen_at,
            "detection_paused": detection_paused,
            "sample_previews": _registration_sample_previews(reg_state),
            "required_poses": progress["required_poses"],
            "current_pose": progress["current_pose"],
            "current_pose_index": progress["current_pose_index"],
            "pose_progress": progress["pose_progress"],
            "total_progress": progress["total_progress"],
            "ready_to_submit": progress["ready_to_submit"],
            "camera_stream": stream_status,
            "status_reason_code": reason_code,
            "status_reason_message": reason_message,
            "status_updated_at": reason_updated_at,
            "session_timeout_seconds": session_timeout_seconds,
            "session_started_at": session_started_at,
            "last_activity_at": last_activity_at,
            "session_expires_at": session_expires_at,
            "seconds_until_expiry": seconds_until_expiry,
        }

    def _registration_error_payload(reg_state, message: str, status_reason_code: str | None = None, **extra):
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](status_reason_code or "registration_error", message)
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
        "quality_threshold": {"min": 0.1, "max": 0.95},
        "recognition_confidence_threshold": {"min": 0.1, "max": 0.99},
        "occupancy_warning_threshold": {"min": 0.5, "max": 0.99},
        "occupancy_snapshot_interval_seconds": {"min": 60, "max": 3600},
        "face_snapshot_retention_days": {"min": 1, "max": 365},
        "recognition_event_retention_days": {"min": 1, "max": 3650},
    }
    SETTINGS_AUDIT_ACTION = "UPDATE_SETTINGS"
    SETTINGS_AUDIT_ROW_LIMIT = 25

    def _format_setting_value(field_name, value):
        if field_name in {"threshold", "recognition_confidence_threshold"}:
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

        face_retention_bounds = SETTINGS_BOUNDS["face_snapshot_retention_days"]
        face_retention_setting = _get_setting(
            deps["db_path"],
            "face_snapshot_retention_days",
            str(getattr(config, "face_snapshot_retention_days", 30)),
        )
        face_snapshot_retention_days = _coerce_int_value(
            face_retention_setting,
            getattr(config, "face_snapshot_retention_days", 30),
        )
        face_snapshot_retention_days = max(
            int(face_retention_bounds["min"]),
            min(int(face_retention_bounds["max"]), face_snapshot_retention_days),
        )
        config.face_snapshot_retention_days = face_snapshot_retention_days

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

        return {
            "threshold": float(threshold),
            "quality_threshold": float(quality_threshold),
            "recognition_confidence_threshold": float(recognition_confidence_threshold),
            "vector_index_top_k": int(vector_index_top_k),
            "max_occupancy": int(max_occupancy),
            "occupancy_warning_threshold": float(occupancy_warning_threshold),
            "occupancy_snapshot_interval_seconds": int(occupancy_snapshot_interval_seconds),
            "face_snapshot_retention_days": int(face_snapshot_retention_days),
            "recognition_event_retention_days": int(recognition_event_retention_days),
            "entry_cctv_stream_source": entry_cctv_stream_source,
            "exit_cctv_stream_source": exit_cctv_stream_source,
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

    def _build_settings_payload(role):
        role_name = str(role or "").strip().lower()
        permissions = _settings_permissions_for_role(role_name)
        settings_state = _read_recognition_settings()
        audit_rows, last_change = _read_settings_audit_rows(permissions["can_view_audit"])
        return {
            "role": role_name,
            "user_count": deps["get_user_count"](),
            "threshold": settings_state["threshold"],
            "quality_threshold": settings_state["quality_threshold"],
            "recognition_confidence_threshold": settings_state["recognition_confidence_threshold"],
            "vector_index_top_k": settings_state["vector_index_top_k"],
            "max_occupancy": settings_state["max_occupancy"],
            "occupancy_warning_threshold": settings_state["occupancy_warning_threshold"],
            "occupancy_snapshot_interval_seconds": settings_state["occupancy_snapshot_interval_seconds"],
            "face_snapshot_retention_days": settings_state["face_snapshot_retention_days"],
            "recognition_event_retention_days": settings_state["recognition_event_retention_days"],
            "entry_cctv_stream_source": settings_state["entry_cctv_stream_source"],
            "exit_cctv_stream_source": settings_state["exit_cctv_stream_source"],
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
                COALESCE(NULLIF(TRIM(u.course), ''), 'Unassigned') AS program,
                SUBSTR(CAST(re.captured_at AS TEXT), 6, 2) AS month_num,
                COUNT(*) AS visit_count
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE re.captured_at IS NOT NULL
              AND SUBSTR(CAST(re.captured_at AS TEXT), 1, 4) = %s
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

        # ── Peak hours (24-slot array, index = hour) ───────────────

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
                        SELECT u.name, u.sr_code, COUNT(re.id) as visits
                        FROM recognition_events re
                        LEFT JOIN users u ON re.user_id = u.user_id
                        WHERE re.captured_at IS NOT NULL
                            AND DATE(re.captured_at) BETWEEN %s AND %s
                        GROUP BY re.user_id, u.name, u.sr_code
            ORDER BY visits DESC
            LIMIT 10
        """, range_params)
        top_visitors = [
            {"name": row[0], "sr_code": row[1], "visits": row[2]}
            for row in c.fetchall()
        ]
        
        # ── Weekly Heatmap (Day 0=Mon to 6=Sun, Hours 7AM–7PM) ──
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

    @bp.route("/settings", methods=["GET"], endpoint="settings")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def settings():
        return _spa_index()

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

            if os.path.exists(deps["base_save_dir"]):
                shutil.rmtree(deps["base_save_dir"])

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

    @bp.route("/api/reset_registration", methods=["POST"], endpoint="reset_registration")
    @login_required
    @role_required("super_admin", "library_admin")
    def reset_registration():
        deps["reset_registration_state"]()
        return {"success": True, "message": "Registration state reset"}

    @bp.route("/api/register-info", methods=["GET"], endpoint="api_register_info")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_info():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        return jsonify(_registration_status_payload(reg_state))

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

    @bp.route("/api/register-reset", methods=["POST"], endpoint="api_register_reset")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_reset():
        deps["reset_registration_state"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "session_reset",
                "Registration capture session reset.",
            )
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
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_session_start():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        heartbeat_ttl_seconds = int(getattr(deps["config"], "registration_worker_heartbeat_ttl_seconds", 10) or 10)
        worker_online_checker = deps.get("is_worker_online")
        if callable(worker_online_checker):
            worker_attached = bool(worker_online_checker("entry", heartbeat_ttl_seconds))
        else:
            worker_attached = bool(deps.get("worker_runtime_attached"))
        if not worker_attached:
            payload = _registration_error_payload(
                reg_state,
                "Entry recognition worker is offline. Start the entry worker and wait for heartbeat sync before starting registration.",
                status_reason_code="worker_unattached",
            )
            return jsonify(payload), 503
        if reg_state.in_progress and reg_state.pending_registration:
            payload = _registration_error_payload(
                reg_state,
                "A registration capture is already complete. Submit it or reset before starting a new session.",
                status_reason_code="capture_complete",
            )
            return jsonify(payload), 409

        if reg_state.manual_active:
            payload = _registration_error_payload(
                reg_state,
                "A registration capture is already in progress.",
                status_reason_code="capture_in_progress",
            )
            return jsonify(payload), 409

        if reg_state.web_session_active or reg_state.manual_requested:
            payload = _registration_error_payload(
                reg_state,
                "A registration session is already active and waiting for a student.",
                status_reason_code="session_already_active",
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
            if deps.get("set_registration_status_reason"):
                deps["set_registration_status_reason"](
                    "session_start_failed",
                    payload["message"],
                )
            return jsonify(payload), 409

        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "session_started",
                "Registration session started. Keep the student in frame to capture required samples.",
            )
        payload.update(
            {
                "success": True,
                "message": "Registration session started. Keep the student in frame to capture required samples.",
            }
        )
        return jsonify(payload)

    @bp.route("/api/register-session/cancel", methods=["POST"], endpoint="api_register_session_cancel")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_session_cancel():
        deps["expire_registration_session_if_needed"]()
        deps["cancel_web_registration_session"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "session_canceled",
                "Registration session canceled.",
            )
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        payload.update(
            {
                "success": True,
                "message": "Registration session canceled.",
            }
        )
        return jsonify(payload)

    @bp.route("/api/register-session/continue-unknown", methods=["POST"], endpoint="api_register_session_continue_unknown")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_session_continue_unknown():
        deps["expire_registration_session_if_needed"]()
        reg_state = deps["get_registration_state"]()
        if not (reg_state.manual_active or reg_state.web_session_active or reg_state.capture_count > 0):
            payload = _registration_error_payload(
                reg_state,
                "No active registration capture is in progress.",
                status_reason_code="no_active_capture",
            )
            return jsonify(payload), 409

        if deps.get("enable_unknown_registration_override"):
            deps["enable_unknown_registration_override"]()
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "override_forced_unknown",
                "Manual override enabled. Continuing registration as an unknown student.",
            )
        reg_state = deps["get_registration_state"]()
        payload = _registration_status_payload(reg_state)
        payload.update(
            {
                "success": True,
                "message": "Continuing capture as a new student by manual override.",
            }
        )
        return jsonify(payload)

    @bp.route("/register", methods=["POST"], endpoint="register_submit")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
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
        if deps.get("set_registration_status_reason"):
            deps["set_registration_status_reason"](
                "registration_submitted",
                f"Profile registered for {saved_user.name}." if saved_user else "Registration saved successfully.",
            )
        total_embeddings = count_embeddings(normalize_embeddings_by_model(all_embeddings))
        redirect_url = url_for("spa_public_routes")
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

    @bp.route("/api/register/unrecognized", methods=["POST"], endpoint="api_register_unrecognized")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_unrecognized():
        payload = request.get_json(silent=True) or {}
        event_id = str(payload.get("event_id") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        if not event_id:
            return jsonify({"success": False, "message": "`event_id` is required."}), 400
        if action not in {"approve", "deny"}:
            return jsonify({"success": False, "message": "`action` must be 'approve' or 'deny'."}), 400

        performer = (session.get("username") or session.get("role") or "staff")
        if action == "deny":
            _insert_user_registration_audit(
                registration_type="unrecognized",
                flow_type="manual_entry",
                status="denied",
                performed_by=str(performer),
                event_id=event_id,
                notes=str(payload.get("notes") or "").strip() or None,
            )
            emit_analytics_update("unrecognized_denied", {"event_id": event_id})
            return jsonify({"success": True, "event_id": event_id, "status": "denied"})

        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "message": "`name` is required for approval."}), 400

        sr_code = str(payload.get("sr_code") or "").strip() or None
        gender = str(payload.get("gender") or "").strip() or ""
        program = str(payload.get("program") or "").strip() or ""
        resolved_type = str(payload.get("user_type") or "unrecognized").strip().lower()
        if resolved_type not in {"enrolled", "visitor", "unrecognized"}:
            resolved_type = "unrecognized"
        captured_at = _parse_iso_utc(payload.get("captured_at"))
        approved_event_id = f"manual-{uuid.uuid4().hex}"

        user_id = _create_identity_user(
            name=name,
            sr_code=sr_code,
            gender=gender,
            program=program,
            user_type=resolved_type,
            flow_type="manual_entry",
        )
        _insert_user_registration_audit(
            user_id=user_id,
            event_id=event_id,
            registration_type="unrecognized",
            flow_type="manual_entry",
            status="approved",
            performed_by=str(performer),
            notes=str(payload.get("notes") or "").strip() or None,
        )
        _record_manual_entry_event(user_id=user_id, sr_code=sr_code, event_id=approved_event_id, captured_at=captured_at)
        bump_profiles_version(deps["db_path"])
        return jsonify(
            {
                "success": True,
                "status": "approved",
                "event_id": event_id,
                "admitted_event_id": approved_event_id,
                "user_id": int(user_id),
                "user_type": resolved_type,
            }
        )

    @bp.route("/api/register/visitor", methods=["POST"], endpoint="api_register_visitor")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_visitor():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "message": "`name` is required."}), 400
        sr_code = str(payload.get("sr_code") or "").strip() or None
        gender = str(payload.get("gender") or "").strip() or ""
        program = str(payload.get("program") or "").strip() or ""
        captured_at = _parse_iso_utc(payload.get("captured_at"))
        performer = (session.get("username") or session.get("role") or "staff")
        event_id = str(payload.get("event_id") or "").strip() or f"visitor-{uuid.uuid4().hex}"

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
                flow_type="manual_entry",
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
        _insert_user_registration_audit(
            user_id=user_id,
            event_id=event_id,
            registration_type="visitor",
            flow_type="manual_entry",
            status="approved",
            performed_by=str(performer),
            notes=str(payload.get("notes") or "").strip() or None,
        )
        _record_manual_entry_event(user_id=user_id, sr_code=sr_code, event_id=event_id, captured_at=captured_at)
        bump_profiles_version(deps["db_path"])
        return jsonify(
            {
                "success": True,
                "user_id": int(user_id),
                "event_id": event_id,
                "user_type": "visitor",
                "flow_type": "manual_entry",
            }
        )

    @bp.route("/api/register/visitor/exit", methods=["POST"], endpoint="api_register_visitor_exit")
    @api_login_required
    @api_role_required("super_admin", "library_admin", "library_staff")
    def api_register_visitor_exit():
        payload = request.get_json(silent=True) or {}
        raw_user_id = payload.get("user_id")
        sr_code = str(payload.get("sr_code") or "").strip() or None
        if raw_user_id is None and not sr_code:
            return jsonify({"success": False, "message": "`user_id` or `sr_code` is required."}), 400

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

        captured_at = _parse_iso_utc(payload.get("captured_at"))
        event_id = str(payload.get("event_id") or "").strip() or f"visitor-exit-{uuid.uuid4().hex}"
        performer = (session.get("username") or session.get("role") or "staff")
        presence = _visitor_presence_state(int(user_id))
        if not presence["inside_now"]:
            return jsonify(
                {
                    "success": False,
                    "message": "Visitor is not currently marked inside.",
                    **presence,
                }
            ), 409

        _insert_user_registration_audit(
            user_id=user_id,
            event_id=event_id,
            registration_type="visitor",
            flow_type="manual_entry",
            status="approved",
            performed_by=str(performer),
            notes="Manual visitor exit confirmation.",
        )
        _record_manual_exit_event(user_id=user_id, sr_code=sr_code, event_id=event_id, captured_at=captured_at)
        return jsonify(
            {
                "success": True,
                "event_id": event_id,
                "user_id": int(user_id),
                "flow_type": "manual_entry",
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
            SELECT
                u.name,
                u.sr_code,
                u.course,
                COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') AS event_type,
                re.confidence,
                COALESCE(re.captured_at, re.ingested_at) AS event_time,
                CASE
                    WHEN COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') = 'entry'
                    THEN COALESCE(re.captured_at, re.ingested_at)
                    ELSE NULL
                END AS entered_at,
                CASE
                    WHEN COALESCE(NULLIF(TRIM(re.event_type), ''), 'entry') = 'exit'
                    THEN COALESCE(re.captured_at, re.ingested_at)
                    ELSE NULL
                END AS exited_at
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
        """
        params = []
        if selected_date:
            query += " WHERE DATE(COALESCE(re.captured_at, re.ingested_at)) = %s"
            params.append(selected_date)
        query += " ORDER BY COALESCE(re.captured_at, re.ingested_at) DESC"

        c.execute(query, params)
        logs = c.fetchall()
        conn.close()
 
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "SR Code", "Program", "Event Type", "Confidence (%)", "Entered At", "Exited At", "Timestamp"])
        for name, sr_code, program, event_type, confidence, event_time, entered_at, exited_at in logs:
            confidence = _coerce_confidence(confidence)
            conf_value = f"{confidence * 100:.1f}" if isinstance(confidence, (int, float)) else ""
            writer.writerow([name, sr_code, program, event_type, conf_value, entered_at, exited_at, event_time])
 
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
                        "quality_threshold",
                        "recognition_confidence_threshold",
                        "face_snapshot_retention_days",
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
                face_retention_bounds = SETTINGS_BOUNDS["face_snapshot_retention_days"]
                face_retention_value, face_retention_error = _parse_bounded_int_payload(
                    payload,
                    "face_snapshot_retention_days",
                    int(face_retention_bounds["min"]),
                    int(face_retention_bounds["max"]),
                )
                if face_retention_error:
                    return jsonify({"success": False, "message": face_retention_error}), 400
                if face_retention_value is not None:
                    next_settings["face_snapshot_retention_days"] = face_retention_value
                    if face_retention_value != current_settings["face_snapshot_retention_days"]:
                        changed_fields["face_snapshot_retention_days"] = (
                            current_settings["face_snapshot_retention_days"],
                            face_retention_value,
                        )

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
                    _set_setting(deps["db_path"], setting_key, next_settings[setting_key])
                deps["set_thresholds"](
                    float(next_settings["threshold"]),
                    float(next_settings["quality_threshold"]),
                )
                deps["config"].vector_index_top_k = int(next_settings["vector_index_top_k"])
                deps["config"].recognition_confidence_threshold = float(
                    next_settings["recognition_confidence_threshold"]
                )
                deps["config"].max_library_capacity = int(next_settings["max_occupancy"])
                deps["config"].occupancy_warning_threshold = float(next_settings["occupancy_warning_threshold"])
                deps["config"].occupancy_snapshot_interval_seconds = int(
                    next_settings["occupancy_snapshot_interval_seconds"]
                )
                deps["config"].face_snapshot_retention_days = int(next_settings["face_snapshot_retention_days"])
                deps["config"].recognition_event_retention_days = int(
                    next_settings["recognition_event_retention_days"]
                )
                deps["config"].entry_cctv_stream_source = str(next_settings["entry_cctv_stream_source"])
                deps["config"].exit_cctv_stream_source = str(next_settings["exit_cctv_stream_source"])
                bump_settings_version(deps["db_path"])

                ordered_keys = [
                    "threshold",
                    "quality_threshold",
                    "recognition_confidence_threshold",
                    "vector_index_top_k",
                    "max_occupancy",
                    "occupancy_warning_threshold",
                    "occupancy_snapshot_interval_seconds",
                    "face_snapshot_retention_days",
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
    @role_required("super_admin", "library_admin")
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
            SELECT user_id, name, sr_code, gender, course AS program, created_at, last_updated, archived_at
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
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
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
    @role_required("super_admin", "library_admin")
    def api_profiles_delete(user_id):
        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        c.execute("SELECT name, sr_code, archived_at FROM users WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Profile not found."}), 404

        name = row[0] or "-"
        sr_code = row[1] or "-"
        archived_at = row[2]
        if archived_at is None or not str(archived_at).strip():
            conn.close()
            return jsonify({"success": False, "message": "Active profiles must be archived before deletion."}), 409

        try:
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
                "created_at": _normalize_timestamp_for_json(row[5], "-"),
                "last_updated": _normalize_timestamp_for_json(row[6], "-"),
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
            WHERE user_id IN ({",".join("%s" for _ in user_ids)})
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
                    COALESCE(u.name, '-') AS name,
                    COALESCE(e.sr_code, u.sr_code, '-') AS sr_code,
                    COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                    COALESCE(e.confidence, 0.0) AS confidence,
                    COALESCE(NULLIF(TRIM(e.event_type), ''), 'entry') AS event_type,
                    COALESCE(e.captured_at, e.ingested_at) AS event_time,
                    COALESCE(e.decision, 'allowed') AS decision
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
            if source_mode == "enhanced" and len(raw) >= 10:
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
                ) = raw
                event_type = str(event_type or "").strip().lower() or "entry"
                if event_type not in {"entry", "exit"}:
                    event_type = "unknown"
                normalized_user_type = str(user_type or "unrecognized").strip().lower() or "unrecognized"
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
                "name": name or "-",
                "sr_code": sr_code or "-",
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
