from __future__ import annotations

import base64
import threading
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

from core.models import User
from workers.api_client import ApiRequestError


class WorkerApiRepository:
    def __init__(self, api_client, outbound_queue, station_id: str = "entry-station-1", camera_id: int = 1):
        self.api_client = api_client
        self.outbound_queue = outbound_queue
        self.station_id = str(station_id or "entry-station-1")
        self.camera_id = int(camera_id or 1)
        self.last_profiles_version = 0
        self._drain_lock = threading.Lock()

    def init_db(self) -> None:
        return None

    def get_all_users(self) -> list[User]:
        snapshot = self.api_client.get_json("/api/internal/profiles/snapshot")
        self.last_profiles_version = int(snapshot.get("profiles_version") or 0)

        users: list[User] = []
        for row in snapshot.get("users") or []:
            embeddings_by_model: dict[str, list[np.ndarray]] = {}
            for model_name, vectors in (row.get("embeddings") or {}).items():
                arrs: list[np.ndarray] = []
                for vector in vectors or []:
                    try:
                        arr = np.asarray(vector, dtype=np.float32)
                    except Exception:
                        continue
                    if arr.ndim == 1 and arr.size > 0:
                        arrs.append(arr)
                if arrs:
                    embeddings_by_model[str(model_name)] = arrs

            users.append(
                User(
                    id=int(row.get("user_id") or 0),
                    name=str(row.get("name") or ""),
                    sr_code=str(row.get("sr_code") or ""),
                    gender=str(row.get("gender") or ""),
                    program=str(row.get("program") or ""),
                    embeddings=embeddings_by_model,
                    image_paths=list(row.get("image_paths") or []),
                    embedding_dim=int(row.get("embedding_dim") or 0),
                )
            )
        return users

    def get_user_by_sr_code(self, sr_code: str):
        return None

    def get_user_by_id(self, user_id: int):
        return None

    def log_recognition(self, result, face_quality: float | None = None, method: str = "two-factor") -> None:
        self.log_decision(
            user_id=int(result.user_id),
            sr_code=result.user.sr_code,
            decision="allowed",
            confidence=float(result.confidence),
            primary_confidence=float(result.primary_confidence),
            secondary_confidence=float(result.secondary_confidence),
            primary_distance=float(result.primary_distance),
            secondary_distance=float(result.secondary_distance),
            face_quality=face_quality,
            method=method,
        )

    def log_unrecognized_detection(
        self,
        *,
        event_id: str,
        track_id: int | None = None,
        face_quality: float | None = None,
        confidence: float | None = None,
        match_threshold: float | None = None,
        method: str = "immediate-unrecognized",
    ) -> None:
        event_type = "exit" if int(self.camera_id) == 2 else "entry"
        payload = {
            "event_id": str(event_id),
            "event_type": event_type,
            "station_id": self.station_id,
            "camera_id": self.camera_id,
            "user_id": None,
            "sr_code": None,
            "decision": "unknown",
            "confidence": float(confidence) if confidence is not None else None,
            "primary_confidence": None,
            "secondary_confidence": None,
            "primary_distance": None,
            "secondary_distance": None,
            "face_quality": float(face_quality) if face_quality is not None else None,
            "method": method,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "worker_timestamp_ms": int(time.time() * 1000),
            "identity_user_type": "unrecognized",
            "identity_name": "Unrecognized User",
            "identity_sr_code": "",
            "track_id": int(track_id) if track_id is not None else None,
            "snapshot_metadata": {"track_id": int(track_id) if track_id is not None else None},
            "match_threshold": float(match_threshold) if match_threshold is not None else None,
        }
        self.outbound_queue.enqueue("recognition_event", payload)

    def revoke_unrecognized_detection(
        self,
        *,
        event_id: str,
        track_id: int | None = None,
        recognized_user: dict | None = None,
        reason: str = "recognized_same_track",
        revoked_at: float | None = None,
    ) -> None:
        payload = {
            "event_id": str(event_id),
            "track_id": int(track_id) if track_id is not None else None,
            "recognized_user": dict(recognized_user or {}),
            "reason": str(reason or "recognized_same_track"),
            "revoked_at": datetime.fromtimestamp(
                float(revoked_at if revoked_at is not None else time.time()),
                timezone.utc,
            ).isoformat(),
            "station_id": self.station_id,
            "camera_id": int(self.camera_id),
        }
        self.outbound_queue.enqueue("recognition_event_revocation", payload)

    def log_decision(
        self,
        *,
        user_id: int,
        sr_code: str | None,
        decision: str,
        confidence: float | None = None,
        primary_confidence: float | None = None,
        secondary_confidence: float | None = None,
        primary_distance: float | None = None,
        secondary_distance: float | None = None,
        face_quality: float | None = None,
        method: str = "two-factor",
        rejection_reason: str | None = None,
    ) -> None:
        event_type = "exit" if int(self.camera_id) == 2 else "entry"
        payload = {
            "event_id": f"evt-{uuid.uuid4().hex}",
            "event_type": event_type,
            "station_id": self.station_id,
            "camera_id": self.camera_id,
            "user_id": int(user_id),
            "sr_code": sr_code,
            "decision": str(decision),
            "confidence": float(confidence) if confidence is not None else None,
            "primary_confidence": float(primary_confidence) if primary_confidence is not None else None,
            "secondary_confidence": float(secondary_confidence) if secondary_confidence is not None else None,
            "primary_distance": float(primary_distance) if primary_distance is not None else None,
            "secondary_distance": float(secondary_distance) if secondary_distance is not None else None,
            "face_quality": float(face_quality) if face_quality is not None else None,
            "method": method,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "worker_timestamp_ms": int(time.time() * 1000),
        }
        if rejection_reason:
            payload["rejection_reason"] = str(rejection_reason)
        self.outbound_queue.enqueue("recognition_event", payload)

    def check_entry_capacity_gate(self) -> dict:
        return self.api_client.get_json("/api/internal/capacity-gate")

    def check_presence_gate(self, user_id: int, event_type: str) -> dict:
        normalized_event_type = str(event_type or "entry").strip().lower() or "entry"
        if normalized_event_type not in {"entry", "exit"}:
            normalized_event_type = "entry"
        path = f"/api/internal/presence-gate?user_id={int(user_id)}&event_type={normalized_event_type}"
        return self.api_client.get_json(path)

    def update_embeddings(self, user_id: int, new_embeddings: dict[str, list[np.ndarray]], image_path: str | None = None):
        payload_embeddings: dict[str, list[list[float]]] = {}
        for model_name, vectors in (new_embeddings or {}).items():
            serializable: list[list[float]] = []
            for vector in vectors or []:
                if not isinstance(vector, np.ndarray):
                    continue
                if vector.ndim != 1 or vector.size == 0:
                    continue
                serializable.append(vector.astype(np.float32, copy=False).tolist())
            if serializable:
                payload_embeddings[str(model_name)] = serializable

        if payload_embeddings:
            self.outbound_queue.enqueue(
                "embedding_update",
                {
                    "user_id": int(user_id),
                    "embeddings": payload_embeddings,
                    "image_path": image_path,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        return None

    def post_registration_sample(
        self,
        *,
        sample_id: str,
        session_id: str,
        pose: str,
        quality: float,
        face_crop: np.ndarray,
        embeddings: dict[str, list[np.ndarray]],
    ) -> dict | None:
        if face_crop is None or getattr(face_crop, "size", 0) == 0:
            print("[REG-SAMPLE][LOCAL-SKIP] empty face_crop")
            return None

        success, encoded = cv2.imencode(".jpg", face_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not success:
            print("[REG-SAMPLE][LOCAL-SKIP] jpeg encode failed")
            return None

        payload_embeddings: dict[str, list[list[float]]] = {}
        for model_name, vectors in (embeddings or {}).items():
            serialized_vectors: list[list[float]] = []
            for vector in vectors or []:
                if not isinstance(vector, np.ndarray):
                    continue
                if vector.ndim != 1 or vector.size == 0:
                    continue
                serialized_vectors.append(vector.astype(np.float32, copy=False).tolist())
            if serialized_vectors:
                payload_embeddings[str(model_name)] = serialized_vectors
        if not payload_embeddings:
            print("[REG-SAMPLE][LOCAL-SKIP] no valid embeddings")
            return None

        payload = {
            "sample_id": str(sample_id or f"sample-{uuid.uuid4().hex}"),
            "session_id": str(session_id or "").strip(),
            "pose": str(pose or "").strip().lower(),
            "quality": float(quality),
            "face_jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "embeddings": payload_embeddings,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "worker_role": "exit" if int(self.camera_id) == 2 else "entry",
            "station_id": self.station_id,
            "camera_id": int(self.camera_id),
        }
        if not payload["session_id"] or payload["pose"] not in {"front", "left", "right"}:
            print(
                f"[REG-SAMPLE][LOCAL-SKIP] invalid payload session_id='{payload['session_id']}' pose='{payload['pose']}'"
            )
            return None

        response = self.api_client.post_json("/api/internal/registrations/samples", payload)
        print(
            "[REG-SAMPLE][LOCAL-ACK] "
            f"sample_id={payload['sample_id']} session_id={payload['session_id']} "
            f"capture_count={response.get('capture_count')} ready={bool(response.get('ready_to_submit'))}"
        )
        return response

    def send_outbound_entry(self, entry: dict) -> bool:
        kind = str(entry.get("kind") or "")
        payload = entry.get("payload") or {}

        if kind == "recognition_event":
            response = self.api_client.post_json("/api/internal/recognition-events", payload)
            return bool(response.get("success"))
        if kind == "recognition_event_revocation":
            response = self.api_client.post_json("/api/internal/recognition-events/revoke", payload)
            return bool(response.get("success"))
        if kind == "embedding_update":
            try:
                response = self.api_client.post_json("/api/internal/embedding-updates", payload)
            except ApiRequestError as exc:
                if exc.status == 404:
                    print(
                        "[EMBED-UPDATE][DROP] "
                        f"status={exc.status} queue_entry_id={entry.get('id')} reason=user_not_found"
                    )
                    return True
                raise
            return bool(response.get("success"))
        return True

    def drain_outbound_queue(self) -> tuple[int, int]:
        with self._drain_lock:
            return self.outbound_queue.drain_once(self.send_outbound_entry)

