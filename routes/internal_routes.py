from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import numpy as np
from flask import Blueprint, jsonify, request

from app.realtime import emit_analytics_update, emit_capacity_threshold_alert, emit_unrecognized_detection
from core.config import QUALITY_CONTEXTS, QUALITY_PROFILE_BOUNDS, QUALITY_PROFILE_FIELDS
from core.models import User
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


def _coerce_quality_field(field_name: str, value, fallback):
    try:
        if field_name in {"quality_face_area_min", "quality_face_area_good"}:
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _quality_setting_key(context: str, field_name: str) -> str:
    return f"{context}_quality_{field_name}"


def _resolve_face_quality_profiles(db_path: str, config, legacy_quality_threshold: float | None = None) -> dict:
    profiles = {}
    for context in QUALITY_CONTEXTS:
        base = config.quality_profile_for_context(context).to_dict()
        for field_name in QUALITY_PROFILE_FIELDS:
            fallback = (
                legacy_quality_threshold
                if field_name == "face_quality_threshold" and legacy_quality_threshold is not None
                else base[field_name]
            )
            raw_value = get_app_setting(db_path, _quality_setting_key(context, field_name), str(fallback))
            parsed = _coerce_quality_field(field_name, raw_value, fallback)
            bounds = QUALITY_PROFILE_BOUNDS[field_name]
            base[field_name] = max(bounds["min"], min(bounds["max"], parsed))
        profiles[context] = base
    config.apply_quality_profiles(profiles)
    return profiles


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


def _parse_utc_datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_daily_occupancy_state(row, fallback_state_date: str, fallback_updated_at: datetime) -> dict:
    if not row:
        return {
            "state_date": fallback_state_date,
            "daily_entries": 0,
            "daily_exits": 0,
            "occupancy_count": 0,
            "updated_at": fallback_updated_at.isoformat(),
        }

    tracked_entries = int(row[1] or 0)
    tracked_exits = int(row[2] or 0)
    return {
        "state_date": row[0],
        "daily_entries": tracked_entries,
        "daily_exits": tracked_exits,
        "occupancy_count": max(0, tracked_entries - tracked_exits),
        "updated_at": row[3],
    }


def _upsert_daily_occupancy_state(cursor, captured_at: datetime, event_type: str) -> dict:
    state_date = captured_at.date().isoformat()
    daily_entries = 1 if event_type == "entry" else 0
    daily_exits = 1 if event_type == "exit" else 0
    cursor.execute(
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
    cursor.execute(
        """
        SELECT state_date, daily_entries, daily_exits, updated_at
        FROM daily_occupancy_state
        WHERE state_date = %s
        """,
        (state_date,),
    )
    return _build_daily_occupancy_state(cursor.fetchone(), state_date, captured_at)


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

    def _entry_event_cooldown_seconds() -> float:
        # Reuse recognition event lock as the canonical entry cooldown knob.
        return max(0.0, float(getattr(deps["config"], "recognition_event_lock_seconds", 8) or 8.0))

    def _resolve_user_presence(cursor, user_id: int) -> dict:
        cursor.execute(
            """
            SELECT
                MAX(CASE WHEN event_type = 'entry' AND decision = 'allowed' THEN COALESCE(captured_at, ingested_at) END) AS last_entry_at,
                MAX(CASE WHEN event_type = 'exit' AND decision = 'allowed' THEN COALESCE(captured_at, ingested_at) END) AS last_exit_at
            FROM recognition_events
            WHERE user_id = %s
            """,
            (int(user_id),),
        )
        row = cursor.fetchone() or (None, None)
        last_entry_at = row[0]
        last_exit_at = row[1]
        inside_now = bool(last_entry_at) and (last_exit_at is None or last_exit_at < last_entry_at)
        return {
            "last_entry_at": last_entry_at,
            "last_exit_at": last_exit_at,
            "inside_now": inside_now,
        }

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

    def _get_occupancy_view() -> tuple[dict, float]:
        config = deps["config"]
        warning_threshold = _resolve_warning_threshold(deps["db_path"], config.occupancy_warning_threshold)
        occupancy_view = OccupancyService(deps["db_path"]).get_current_occupancy(
            _runtime_capacity_limit(),
            warning_threshold=warning_threshold,
        )
        return occupancy_view, warning_threshold

    def _evaluate_occupancy_alert(occupancy_view: dict, warning_threshold: float) -> tuple[dict, bool]:
        return occupancy_alert_service.evaluate(
            occupancy_count=int(occupancy_view["occupancy_count"]),
            capacity_limit=int(occupancy_view["capacity_limit"]),
            occupancy_ratio=float(occupancy_view["occupancy_ratio"]),
            is_full=bool(occupancy_view["is_full"]),
            capacity_warning=bool(occupancy_view["capacity_warning"]),
            warning_threshold=float(warning_threshold),
            moderate_threshold=max(0.0, float(warning_threshold) * 0.75),
            state_is_stale=False,
        )

    def _persist_capacity_alert_from_level(level: str, occupancy_view: dict) -> dict | None:
        alert_service = AlertService(deps["db_path"])
        if str(level or "").strip().lower() == "full":
            return alert_service.create_capacity_reached_alert(
                occupancy_count=int(occupancy_view["occupancy_count"]),
                capacity_limit=int(occupancy_view["capacity_limit"]),
            )
        if str(level or "").strip().lower() == "warning":
            return alert_service.create_capacity_warning_alert(
                occupancy_count=int(occupancy_view["occupancy_count"]),
                capacity_limit=int(occupancy_view["capacity_limit"]),
            )
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
        registration_control_getter = deps.get("get_registration_control")
        if callable(registration_control_getter):
            registration_control = registration_control_getter()
        else:
            reg_state = deps["get_registration_state"]() if deps.get("get_registration_state") else None
            registration_control = {
                "session_id": str(getattr(reg_state, "session_id", "") or "") or None,
                "phase": str(getattr(reg_state, "phase", "idle") or "idle"),
                "expected_pose": str(getattr(reg_state, "current_pose", "") or "") or None,
                "force_new_identity": bool(getattr(reg_state, "force_new_identity", False)),
                "registration_kind": str(getattr(reg_state, "registration_kind", "student") or "student"),
            }
        registration_progress_getter = deps.get("get_registration_progress")
        registration_progress = registration_progress_getter() if callable(registration_progress_getter) else None
        return jsonify(
            {
                "settings_version": get_settings_version(deps["db_path"]),
                "base_threshold": float(threshold),
                "face_quality_threshold": float(quality_threshold),
                "face_quality_profiles": _resolve_face_quality_profiles(
                    deps["db_path"],
                    config,
                    legacy_quality_threshold=float(quality_threshold),
                ),
                "vector_index_top_k": int(config.vector_index_top_k),
                "recognition_confidence_threshold": float(config.recognition_confidence_threshold),
                "entry_cctv_stream_source": str(config.entry_cctv_stream_source),
                "exit_cctv_stream_source": str(config.exit_cctv_stream_source),
                "registration_control": registration_control,
                "registration_progress": registration_progress,
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

        captured_at = _parse_utc_datetime(payload.get("captured_at"))

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
            elif user_id is not None:
                # Guard against stale worker user ids that no longer exist in host DB.
                c.execute("SELECT 1 FROM users WHERE user_id = %s", (int(user_id),))
                exists = c.fetchone() is not None
                if not exists:
                    fallback_user_id = None
                    if sr_code:
                        c.execute("SELECT user_id FROM users WHERE sr_code = %s", (sr_code,))
                        fallback_row = c.fetchone()
                        if fallback_row:
                            fallback_user_id = int(fallback_row[0])
                    if fallback_user_id is not None:
                        print(
                            "[RECOGNITION-EVENT][USER-ID-REMAP] "
                            f"event_id={event_id} stale_user_id={user_id} remapped_user_id={fallback_user_id} sr_code={sr_code}"
                        )
                        user_id = fallback_user_id
                    else:
                        print(
                            "[RECOGNITION-EVENT][USER-ID-DROPPED] "
                            f"event_id={event_id} stale_user_id={user_id} sr_code={sr_code}"
                        )
                        user_id = None

            blocked_reason = None
            cooldown_remaining_seconds = None
            if decision == "allowed" and user_id is not None and event_type == "entry":
                presence = _resolve_user_presence(c, int(user_id))
                if presence["inside_now"]:
                    blocked_reason = "already_inside"
                else:
                    cooldown_seconds = _entry_event_cooldown_seconds()
                    last_entry_at = presence["last_entry_at"]
                    last_exit_at = presence["last_exit_at"]
                    if cooldown_seconds > 0 and isinstance(last_entry_at, datetime):
                        # Cooldown applies only while a new entry has not yet been closed by exit.
                        # If exit is detected after the last entry, cooldown is considered reset.
                        cooldown_reset_by_exit = isinstance(last_exit_at, datetime) and last_exit_at >= last_entry_at
                        if not cooldown_reset_by_exit:
                            elapsed = (captured_at - last_entry_at).total_seconds()
                            if elapsed < cooldown_seconds:
                                blocked_reason = "entry_cooldown"
                                cooldown_remaining_seconds = max(0.0, cooldown_seconds - max(0.0, elapsed))

            if blocked_reason:
                decision = "denied"
                payload_for_json["decision"] = "denied"
                payload_for_json["rejection_reason"] = blocked_reason
                if cooldown_remaining_seconds is not None:
                    payload_for_json["cooldown_remaining_seconds"] = round(float(cooldown_remaining_seconds), 2)
                payload_json = json.dumps(payload_for_json, ensure_ascii=True)

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
                occupancy_state = _upsert_daily_occupancy_state(c, captured_at, event_type)

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
            occupancy_state = occupancy_state or _build_daily_occupancy_state(
                None,
                fallback_state_date=captured_at.date().isoformat(),
                fallback_updated_at=captured_at,
            )
            occupancy_view, warning_threshold = _get_occupancy_view()
            alert_payload, alert_changed = _evaluate_occupancy_alert(occupancy_view, warning_threshold)
            persisted_alert = _persist_capacity_alert_from_level(alert_payload.get("level"), occupancy_view)
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
                        "alert": persisted_alert,
                        **alert_payload,
                    }
                )
        elif inserted and decision in {"unknown", "denied"}:
            metadata = payload.get("snapshot_metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            occ_view, _warning_threshold = _get_occupancy_view()
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
                "decision": decision,
                "blocked_reason": payload_for_json.get("rejection_reason"),
                "cooldown_remaining_seconds": payload_for_json.get("cooldown_remaining_seconds"),
            }
        )

    @bp.route("/capacity-gate", methods=["GET"], endpoint="capacity_gate")
    def capacity_gate():
        occ, warning_threshold = _get_occupancy_view()
        allow_entry = True
        monitoring_reason = "capacity_reached_monitoring_only" if bool(occ["is_full"]) else "ok"
        alert_payload, alert_changed = _evaluate_occupancy_alert(occ, warning_threshold)
        alert = _persist_capacity_alert_from_level(alert_payload.get("level"), occ)
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

    @bp.route("/presence-gate", methods=["GET"], endpoint="presence_gate")
    def presence_gate():
        user_id_raw = (request.args.get("user_id") or "").strip()
        event_type = str(request.args.get("event_type") or "entry").strip().lower() or "entry"
        if event_type not in {"entry", "exit"}:
            return _json_error("`event_type` must be 'entry' or 'exit'.", 400)
        if not user_id_raw:
            return _json_error("`user_id` is required.", 400)
        try:
            user_id = int(user_id_raw)
        except ValueError:
            return _json_error("`user_id` must be an integer.", 400)

        conn = db_connect(deps["db_path"])
        c = conn.cursor()
        try:
            presence = _resolve_user_presence(c, user_id)
        finally:
            conn.close()

        if event_type == "entry":
            allow_event = not bool(presence["inside_now"])
            reason = "ok" if allow_event else "already_inside"
        else:
            allow_event = bool(presence["inside_now"])
            reason = "ok" if allow_event else "not_inside"

        return jsonify(
            {
                "success": True,
                "user_id": user_id,
                "event_type": event_type,
                "allow_event": allow_event,
                "reason": reason,
                "inside_now": bool(presence["inside_now"]),
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
