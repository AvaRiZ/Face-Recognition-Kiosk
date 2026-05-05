from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone

import cv2
import numpy as np
from flask import Blueprint, jsonify, request

from app.realtime import emit_analytics_update, emit_capacity_threshold_alert, emit_unrecognized_detection
from core.models import RegistrationSample, User
from db import connect as db_connect
from db import get_app_setting
from services.occupancy_service import OccupancyService, resolve_capacity_limit
from services.alert_service import AlertService
from services.occupancy_alert_service import occupancy_alert_service
from services.versioning_service import bump_profiles_version, get_profiles_version, get_settings_version


def _json_error(message: str, status: int):
    return jsonify({"success": False, "message": message}), status


def _optional_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_warning_threshold(db_path: str, default: float) -> float:
    raw_value = get_app_setting(db_path, "occupancy_warning_threshold", str(default))
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(0.5, min(0.99, parsed))


def _camera_id_from_event_type(event_type: str) -> int | None:
    normalized = str(event_type or "").strip().lower()
    if normalized == "entry":
        return 1
    if normalized == "exit":
        return 2
    return None


def _resolve_event_type(payload: dict) -> tuple[str, int]:
    raw_event_type = str(payload.get("event_type") or "").strip().lower()
    if raw_event_type:
        if raw_event_type not in {"entry", "exit"}:
            raise ValueError("`event_type` must be 'entry' or 'exit'.")
        return raw_event_type, int(_camera_id_from_event_type(raw_event_type) or 1)

    camera_id = _optional_int(payload.get("camera_id"))
    if camera_id is None:
        return "entry", 1
    if camera_id not in {1, 2}:
        raise ValueError("`camera_id` must be 1 for entry or 2 for exit.")
    return ("entry", 1) if camera_id == 1 else ("exit", 2)


def _require_worker_token():
    configured = (os.environ.get("WORKER_INTERNAL_TOKEN") or "").strip()
    if not configured:
        return None
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {configured}"
    if auth != expected:
        return _json_error("Unauthorized internal token.", 401)
    return None


def _serialize_user(user: User) -> dict:
    return {
        "user_id": int(user.id),
        "name": user.name,
        "sr_code": user.sr_code,
        "gender": user.gender,
        "program": user.program,
        "embedding_dim": int(user.embedding_dim or 0),
        "image_paths": list(user.image_paths or []),
        "embeddings": {
            model: [emb.astype(np.float32, copy=False).tolist() for emb in vectors]
            for model, vectors in (user.embeddings or {}).items()
        },
    }


def _deserialize_embeddings(payload_embeddings: dict) -> dict[str, list[np.ndarray]]:
    normalized: dict[str, list[np.ndarray]] = {}
    for model_name, vectors in (payload_embeddings or {}).items():
        vec_list: list[np.ndarray] = []
        for vector in vectors or []:
            try:
                arr = np.asarray(vector, dtype=np.float32)
            except Exception:
                continue
            if arr.ndim == 1 and arr.size > 0:
                vec_list.append(arr)
        if vec_list:
            normalized[str(model_name)] = vec_list
    return normalized


def _decode_face_jpeg_base64(encoded_image: str) -> np.ndarray | None:
    text = str(encoded_image or "").strip()
    if not text:
        return None
    if "," in text and text.lower().startswith("data:"):
        _, text = text.split(",", 1)
    try:
        raw = base64.b64decode(text, validate=True)
    except Exception:
        return None
    if not raw:
        return None
    buffer = np.frombuffer(raw, dtype=np.uint8)
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _parse_observed_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return time.time()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except Exception:
        return time.time()


def create_internal_blueprint(deps):
    bp = Blueprint("internal_routes", __name__, url_prefix="/api/internal")

    @bp.before_request
    def _auth_guard():
        return _require_worker_token()

    def _runtime_capacity_limit() -> int:
        config = deps["config"]
        return resolve_capacity_limit(
            deps["db_path"],
            default=int(config.max_library_capacity),
        )

    def _registration_worker_heartbeat_ttl_seconds() -> int:
        return int(getattr(deps["config"], "registration_worker_heartbeat_ttl_seconds", 10) or 10)

    def _entry_worker_online() -> bool:
        checker = deps.get("is_worker_online")
        if callable(checker):
            return bool(checker("entry", _registration_worker_heartbeat_ttl_seconds()))
        return bool(deps.get("worker_runtime_attached"))

    def _entry_worker_last_seen_at() -> float | None:
        getter = deps.get("get_worker_last_seen_at")
        if callable(getter):
            value = getter("entry")
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @bp.route("/profiles/version", methods=["GET"], endpoint="profiles_version")
    def profiles_version():
        db_path = deps["db_path"]
        return jsonify({"profiles_version": get_profiles_version(db_path)})

    @bp.route("/profiles/snapshot", methods=["GET"], endpoint="profiles_snapshot")
    def profiles_snapshot():
        repository = deps["repository"]
        users = repository.get_all_users()
        return jsonify(
            {
                "profiles_version": get_profiles_version(deps["db_path"]),
                "users": [_serialize_user(user) for user in users],
            }
        )

    @bp.route("/runtime-config", methods=["GET"], endpoint="runtime_config")
    def runtime_config():
        threshold, quality_threshold = deps["get_thresholds"]()
        config = deps["config"]
        reg_state = deps["get_registration_state"]() if deps.get("get_registration_state") else None
        registration_session = {
            "web_session_active": bool(getattr(reg_state, "web_session_active", False)),
            "manual_requested": bool(getattr(reg_state, "manual_requested", False)),
            "manual_active": bool(getattr(reg_state, "manual_active", False)),
            "in_progress": bool(getattr(reg_state, "in_progress", False)),
            "session_id": str(getattr(reg_state, "session_id", "") or "") or None,
            "capture_count": int(getattr(reg_state, "capture_count", 0)),
            "status_reason_code": str(getattr(reg_state, "status_reason_code", "") or ""),
            "status_reason_message": str(getattr(reg_state, "status_reason_message", "") or ""),
        }
        return jsonify(
            {
                "settings_version": get_settings_version(deps["db_path"]),
                "base_threshold": float(threshold),
                "face_quality_threshold": float(quality_threshold),
                "vector_index_top_k": int(config.vector_index_top_k),
                "recognition_confidence_threshold": float(config.recognition_confidence_threshold),
                "entry_cctv_stream_source": str(config.entry_cctv_stream_source),
                "exit_cctv_stream_source": str(config.exit_cctv_stream_source),
                "registration_session": registration_session,
                "entry_worker_online": _entry_worker_online(),
                "entry_worker_last_seen_at": _entry_worker_last_seen_at(),
            }
        )

    @bp.route("/worker-heartbeat", methods=["POST"], endpoint="worker_heartbeat")
    def worker_heartbeat():
        payload = request.get_json(silent=True) or {}
        worker_role = str(payload.get("worker_role") or "").strip().lower() or "entry"
        if worker_role not in {"entry", "exit"}:
            return _json_error("`worker_role` must be 'entry' or 'exit'.", 400)

        camera_id = _optional_int(payload.get("camera_id"))
        station_id = str(payload.get("station_id") or "").strip() or None
        observed_at = _parse_observed_timestamp(payload.get("observed_at"))
        recorder = deps.get("record_worker_heartbeat")
        if not callable(recorder):
            return _json_error("Worker heartbeat storage is unavailable.", 503)
        recorded_at = recorder(
            worker_role=worker_role,
            station_id=station_id,
            camera_id=camera_id,
            observed_at=observed_at,
        )
        return jsonify(
            {
                "success": True,
                "worker_role": worker_role,
                "recorded_at": float(recorded_at),
                "entry_worker_online": _entry_worker_online(),
                "entry_worker_last_seen_at": _entry_worker_last_seen_at(),
            }
        )

    @bp.route("/registration-samples", methods=["POST"], endpoint="registration_samples_ingest")
    def registration_samples_ingest():
        payload = request.get_json(silent=True) or {}
        sample_id = str(payload.get("sample_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        worker_role = str(payload.get("worker_role") or "").strip().lower() or "entry"
        station_id = str(payload.get("station_id") or "").strip() or None
        camera_id = _optional_int(payload.get("camera_id"))
        pose = str(payload.get("pose") or "").strip().lower()
        quality = _optional_float(payload.get("quality"))
        face_jpeg_base64 = payload.get("face_jpeg_base64")
        embeddings = _deserialize_embeddings(payload.get("embeddings") or {})

        if not sample_id:
            return _json_error("`sample_id` is required.", 400)
        if not session_id:
            return _json_error("`session_id` is required.", 400)
        if worker_role != "entry":
            return _json_error("Registration sample ingestion only accepts entry worker payloads.", 403)
        if pose not in {"front", "left", "right"}:
            return _json_error("`pose` must be one of: front, left, right.", 400)
        if quality is None:
            return _json_error("`quality` is required and must be numeric.", 400)
        if not embeddings:
            return _json_error("`embeddings` is required and must contain valid vectors.", 400)

        reg_state_getter = deps.get("get_registration_state")
        capture_sample = deps.get("capture_registration_sample")
        claim_sample = deps.get("claim_registration_sample_id")
        if not callable(reg_state_getter) or not callable(capture_sample) or not callable(claim_sample):
            return _json_error("Registration sample ingestion dependencies are unavailable.", 503)

        reg_state = reg_state_getter()
        active_session_id = str(getattr(reg_state, "session_id", "") or "").strip()
        if not active_session_id:
            return _json_error("No active registration session is available for sample ingestion.", 409)
        if session_id != active_session_id:
            return _json_error("Registration session is stale. Start a new session and retry capture.", 409)

        face_crop = _decode_face_jpeg_base64(face_jpeg_base64)
        if face_crop is None or getattr(face_crop, "size", 0) == 0:
            return _json_error("`face_jpeg_base64` must contain a valid JPEG image.", 400)

        accepted = bool(claim_sample(session_id, sample_id))
        if not accepted:
            return jsonify(
                {
                    "success": True,
                    "duplicate": True,
                    "sample_id": sample_id,
                    "session_id": session_id,
                }
            )

        sample = RegistrationSample(
            face_crop=face_crop,
            embeddings=embeddings,
            quality=float(quality),
            pose=pose,
        )
        capture_count = int(capture_sample(sample))
        reg_state = reg_state_getter()
        return jsonify(
            {
                "success": True,
                "duplicate": False,
                "sample_id": sample_id,
                "session_id": session_id,
                "capture_count": capture_count,
                "max_captures": int(getattr(reg_state, "max_captures", 0)),
                "in_progress": bool(getattr(reg_state, "in_progress", False)),
                "has_pending_registration": bool(getattr(reg_state, "pending_registration", None)),
                "worker_role": worker_role,
                "station_id": station_id,
                "camera_id": camera_id,
            }
        )

    @bp.route("/recognition-events", methods=["POST"], endpoint="recognition_events_ingest")
    def recognition_events_ingest():
        payload = request.get_json(silent=True) or {}
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            return _json_error("`event_id` is required.", 400)

        allowed_decisions = {"allowed", "denied", "unknown"}
        decision = str(payload.get("decision") or "").strip().lower() or "allowed"
        if decision not in allowed_decisions:
            decision = "unknown"

        raw_user_id = payload.get("user_id")
        user_id = None
        if raw_user_id is not None and raw_user_id != "":
            try:
                parsed_user_id = int(raw_user_id)
                user_id = parsed_user_id if parsed_user_id > 0 else None
            except (TypeError, ValueError):
                user_id = None

        sr_code = str(payload.get("sr_code") or "").strip() or None
        try:
            event_type, camera_id = _resolve_event_type(payload)
        except ValueError as exc:
            return _json_error(str(exc), 400)

        captured_at_raw = payload.get("captured_at")
        captured_at = datetime.now(timezone.utc)
        if captured_at_raw:
            text = str(captured_at_raw).strip()
            if text:
                try:
                    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    captured_at = parsed.astimezone(timezone.utc)
                except Exception:
                    captured_at = datetime.now(timezone.utc)

        confidence = _optional_float(payload.get("confidence"))
        primary_confidence = _optional_float(payload.get("primary_confidence"))
        secondary_confidence = _optional_float(payload.get("secondary_confidence"))
        primary_distance = _optional_float(payload.get("primary_distance"))
        secondary_distance = _optional_float(payload.get("secondary_distance"))
        face_quality = _optional_float(payload.get("face_quality"))
        method = str(payload.get("method") or "two-factor")
        entered_at = captured_at if event_type == "entry" else None
        exited_at = captured_at if event_type == "exit" else None
        payload_for_json = dict(payload)
        payload_for_json["event_type"] = event_type
        payload_for_json["captured_at"] = captured_at.isoformat()
        payload_for_json.pop("entered_at", None)
        payload_for_json.pop("exited_at", None)
        payload_for_json.pop("camera_id", None)
        payload_for_json.pop("station_id", None)
        payload_json = json.dumps(payload_for_json, ensure_ascii=True)

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        inserted = False
        occupancy_state = None

        try:
            if user_id is None and sr_code:
                c.execute("SELECT user_id FROM users WHERE sr_code = %s", (sr_code,))
                row = c.fetchone()
                if row:
                    user_id = int(row[0])

            c.execute(
                """
                INSERT INTO recognition_events (
                    event_id, user_id, sr_code, decision, event_type, confidence,
                    primary_confidence, secondary_confidence, primary_distance, secondary_distance,
                    face_quality, method, captured_at, payload_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event_id,
                    user_id,
                    sr_code,
                    decision,
                    event_type,
                    confidence,
                    primary_confidence,
                    secondary_confidence,
                    primary_distance,
                    secondary_distance,
                    face_quality,
                    method,
                    captured_at,
                    payload_json,
                ),
            )
            inserted = (c.rowcount or 0) > 0

            if inserted and decision == "allowed":
                daily_entries = 1 if event_type == "entry" else 0
                daily_exits = 1 if event_type == "exit" else 0
                state_date = captured_at.date().isoformat()
                c.execute(
                    """
                    INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(state_date) DO UPDATE SET
                        daily_entries = daily_occupancy_state.daily_entries + excluded.daily_entries,
                        daily_exits = daily_occupancy_state.daily_exits + excluded.daily_exits,
                        updated_at = excluded.updated_at
                    """,
                    (state_date, daily_entries, daily_exits, captured_at),
                )
                c.execute(
                    """
                    SELECT state_date, daily_entries, daily_exits, updated_at
                    FROM daily_occupancy_state
                    WHERE state_date = %s
                    """,
                    (state_date,),
                )
                row = c.fetchone()
                if row:
                    tracked_entries = int(row[1] or 0)
                    tracked_exits = int(row[2] or 0)
                    occupancy_state = {
                        "state_date": row[0],
                        "daily_entries": tracked_entries,
                        "daily_exits": tracked_exits,
                        "occupancy_count": max(0, tracked_entries - tracked_exits),
                        "updated_at": row[3],
                    }

            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        if inserted and decision == "allowed":
            if event_type == "exit" and user_id:
                try:
                    audit_conn = db_connect(deps["db_path"])
                    audit_cursor = audit_conn.cursor()
                    audit_cursor.execute(
                        "SELECT user_type FROM users WHERE user_id = %s",
                        (user_id,),
                    )
                    user_row = audit_cursor.fetchone()
                    if user_row and str(user_row[0] or "").strip().lower() == "visitor":
                        audit_cursor.execute(
                            """
                            INSERT INTO user_registrations (
                                user_id, event_id, registration_type, flow_type, status, performed_by, notes
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                user_id,
                                event_id,
                                "visitor",
                                "manual_entry",
                                "approved",
                                "system",
                                "Visitor exit recorded by exit worker.",
                            ),
                        )
                        audit_conn.commit()
                except Exception:
                    pass
                finally:
                    try:
                        audit_conn.close()
                    except Exception:
                        pass
            occupancy_service = OccupancyService(deps["db_path"])
            config = deps["config"]
            warning_threshold = _resolve_warning_threshold(deps["db_path"], config.occupancy_warning_threshold)
            if occupancy_state is None:
                occupancy_state = {
                    "daily_entries": 0,
                    "daily_exits": 0,
                    "occupancy_count": 0,
                }
            occupancy_view = occupancy_service.get_current_occupancy(
                _runtime_capacity_limit(),
                warning_threshold=warning_threshold,
            )
            alert_payload, alert_changed = occupancy_alert_service.evaluate(
                occupancy_count=int(occupancy_view["occupancy_count"]),
                capacity_limit=int(occupancy_view["capacity_limit"]),
                occupancy_ratio=float(occupancy_view["occupancy_ratio"]),
                is_full=bool(occupancy_view["is_full"]),
                capacity_warning=bool(occupancy_view["capacity_warning"]),
                warning_threshold=float(warning_threshold),
                moderate_threshold=max(0.0, float(warning_threshold) * 0.75),
                state_is_stale=False,
            )
            emit_analytics_update(
                "recognition_event_ingested",
                {
                    "event_id": event_id,
                    "user_id": user_id,
                    "event_type": event_type,
                    "camera_id": camera_id,
                    "entered_at": entered_at.isoformat() if entered_at else None,
                    "exited_at": exited_at.isoformat() if exited_at else None,
                    "daily_entries": occupancy_state["daily_entries"],
                    "daily_exits": occupancy_state["daily_exits"],
                    "occupancy_count": occupancy_state["occupancy_count"],
                    "capacity_warning": bool(occupancy_view["capacity_warning"]),
                },
            )
            if alert_changed:
                emit_capacity_threshold_alert(
                    {
                        "reason": "occupancy_state_changed",
                        "capacity_warning": bool(occupancy_view["capacity_warning"]),
                        **alert_payload,
                    }
                )
        elif inserted and decision in {"unknown", "denied"}:
            metadata = payload.get("snapshot_metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            try:
                audit_conn = db_connect(deps["db_path"])
                audit_cursor = audit_conn.cursor()
                audit_cursor.execute(
                    """
                    INSERT INTO user_registrations (
                        user_id, event_id, registration_type, flow_type, status, performed_by, notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        event_id,
                        "unrecognized",
                        "manual_entry",
                        "pending",
                        "system",
                        "Pending librarian approval for unrecognized detection.",
                    ),
                )
                audit_conn.commit()
            except Exception:
                pass
            finally:
                try:
                    audit_conn.close()
                except Exception:
                    pass
            config = deps["config"]
            warning_threshold = _resolve_warning_threshold(deps["db_path"], config.occupancy_warning_threshold)
            occ_view = OccupancyService(deps["db_path"]).get_current_occupancy(
                _runtime_capacity_limit(),
                warning_threshold=warning_threshold,
            )
            capacity_warning = bool(occ_view["capacity_warning"])
            emit_unrecognized_detection(
                {
                    "reason": "unrecognized_detection",
                    "event_id": event_id,
                    "event_type": event_type,
                    "camera_id": camera_id,
                    "captured_at": captured_at.isoformat(),
                    "confidence": confidence,
                    "requires_librarian_approval": True,
                    "capacity_warning": capacity_warning,
                    "snapshot_metadata": metadata,
                }
            )
            emit_analytics_update(
                "unrecognized_detection",
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "camera_id": camera_id,
                    "captured_at": captured_at.isoformat(),
                    "capacity_warning": capacity_warning,
                },
            )

        return jsonify(
            {
                "success": True,
                "event_id": event_id,
                "event_type": event_type,
                "camera_id": camera_id,
                "duplicate": not inserted,
            }
        )

    @bp.route("/capacity-gate", methods=["GET"], endpoint="capacity_gate")
    def capacity_gate():
        config = deps["config"]
        warning_threshold = _resolve_warning_threshold(deps["db_path"], config.occupancy_warning_threshold)
        occupancy_service = OccupancyService(deps["db_path"])
        occ = occupancy_service.get_current_occupancy(
            _runtime_capacity_limit(),
            warning_threshold=warning_threshold,
        )
        allow_entry = True
        monitoring_reason = "capacity_reached_monitoring_only" if bool(occ["is_full"]) else "ok"
        alert = None
        if bool(occ["is_full"]):
            alert = AlertService(deps["db_path"]).create_capacity_reached_alert(
                occupancy_count=int(occ["occupancy_count"]),
                capacity_limit=int(occ["capacity_limit"]),
            )
        alert_payload, alert_changed = occupancy_alert_service.evaluate(
            occupancy_count=int(occ["occupancy_count"]),
            capacity_limit=int(occ["capacity_limit"]),
            occupancy_ratio=float(occ["occupancy_ratio"]),
            is_full=bool(occ["is_full"]),
            capacity_warning=bool(occ["capacity_warning"]),
            warning_threshold=float(warning_threshold),
            moderate_threshold=max(0.0, float(warning_threshold) * 0.75),
            state_is_stale=False,
        )
        if alert_changed or alert is not None:
            emit_capacity_threshold_alert(
                {
                    "reason": "capacity_gate_check",
                    "capacity_warning": bool(occ["capacity_warning"]),
                    "alert": alert,
                    **alert_payload,
                }
            )
        emit_analytics_update(
            "capacity_gate_checked",
            {
                "allow_entry": allow_entry,
                "reason": monitoring_reason,
                "occupancy_count": int(occ["occupancy_count"]),
                "capacity_limit": int(occ["capacity_limit"]),
                "occupancy_ratio": float(occ["occupancy_ratio"]),
                "is_full": bool(occ["is_full"]),
                "capacity_warning": bool(occ["capacity_warning"]),
            },
        )
        return jsonify(
            {
                "success": True,
                "allow_entry": allow_entry,
                "reason": monitoring_reason,
                "occupancy_count": int(occ["occupancy_count"]),
                "capacity_limit": int(occ["capacity_limit"]),
                "occupancy_ratio": float(occ["occupancy_ratio"]),
                "is_full": bool(occ["is_full"]),
                "alert": alert,
            }
        )

    @bp.route("/embedding-updates", methods=["POST"], endpoint="embedding_updates")
    def embedding_updates():
        payload = request.get_json(silent=True) or {}
        user_id_raw = payload.get("user_id")
        if user_id_raw is None:
            return _json_error("`user_id` is required.", 400)
        try:
            user_id = int(user_id_raw)
        except Exception:
            return _json_error("`user_id` must be an integer.", 400)

        embeddings = _deserialize_embeddings(payload.get("embeddings") or {})
        if not embeddings:
            return _json_error("`embeddings` is required and must contain valid vectors.", 400)

        image_path = payload.get("image_path")
        updated_user = deps["repository"].update_embeddings(user_id, embeddings, image_path=image_path)
        if updated_user is None:
            return _json_error("User not found for embedding update.", 404)
        bump_profiles_version(deps["db_path"])
        return jsonify({"success": True, "user_id": int(user_id)})

    @bp.route("/occupancy-snapshot", methods=["POST"], endpoint="occupancy_snapshot")
    def occupancy_snapshot():
        """Create an occupancy snapshot (point-in-time occupancy state)."""
        from core.config import AppConfig

        try:
            config = AppConfig()
            service = OccupancyService(config.db_path)
            warning_threshold = _resolve_warning_threshold(config.db_path, config.occupancy_warning_threshold)
            service.create_snapshot(
                resolve_capacity_limit(config.db_path, default=int(config.max_library_capacity)),
                warning_threshold=warning_threshold,
            )
            return jsonify({"success": True, "message": "Occupancy snapshot created."})
        except Exception as exc:
            return _json_error(f"Failed to create snapshot: {str(exc)}", 500)

    return bp
