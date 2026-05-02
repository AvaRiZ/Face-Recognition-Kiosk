from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
from flask import Blueprint, jsonify, request

from app.realtime import emit_analytics_update
from core.models import User
from db import connect as db_connect
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


def create_internal_blueprint(deps):
    bp = Blueprint("internal_routes", __name__, url_prefix="/api/internal")

    @bp.before_request
    def _auth_guard():
        return _require_worker_token()

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
        return jsonify(
            {
                "settings_version": get_settings_version(deps["db_path"]),
                "base_threshold": float(threshold),
                "face_quality_threshold": float(quality_threshold),
                "vector_index_top_k": int(config.vector_index_top_k),
                "recognition_confidence_threshold": float(config.recognition_confidence_threshold),
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
        station_id = str(payload.get("station_id") or "").strip() or "entrance-station-1"
        camera_id = _optional_int(payload.get("camera_id"))
        if camera_id is None:
            camera_id = 1
        if camera_id not in {1, 2}:
            return _json_error("`camera_id` must be 1 for entry or 2 for exit.", 400)

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
        payload_for_json = dict(payload)
        payload_for_json["captured_at"] = captured_at.isoformat()
        payload_for_json["camera_id"] = camera_id
        payload_for_json["station_id"] = station_id
        payload_json = json.dumps(payload_for_json, ensure_ascii=True)

        conn = db_connect(deps["db_path"])
        c = conn.cursor()

        if user_id is None and sr_code:
            c.execute("SELECT user_id FROM users WHERE sr_code = ?", (sr_code,))
            row = c.fetchone()
            if row:
                user_id = int(row[0])

        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, station_id, camera_id, user_id, sr_code, decision, confidence,
                primary_confidence, secondary_confidence, primary_distance, secondary_distance,
                face_quality, method, captured_at, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                event_id,
                station_id,
                camera_id,
                user_id,
                sr_code,
                decision,
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

        conn.commit()
        if inserted and user_id:
            try:
                c.execute(
                    """
                    INSERT INTO recognition_log (
                        user_id, confidence, primary_confidence, secondary_confidence,
                        primary_distance, secondary_distance, face_quality, method
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        confidence or 0.0,
                        primary_confidence,
                        secondary_confidence,
                        primary_distance,
                        secondary_distance,
                        face_quality,
                        method,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
        conn.close()
        if inserted:
            emit_analytics_update(
                "recognition_event_ingested",
                {
                    "event_id": event_id,
                    "user_id": user_id,
                    "camera_id": camera_id,
                },
            )
        return jsonify({"success": True, "event_id": event_id, "duplicate": not inserted})

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

    return bp
