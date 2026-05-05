from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

from core.models import User


class WorkerApiRepository:
    def __init__(self, api_client, outbound_queue, station_id: str = "entry-station-1", camera_id: int = 1):
        self.api_client = api_client
        self.outbound_queue = outbound_queue
        self.station_id = str(station_id or "entry-station-1")
        self.camera_id = int(camera_id or 1)
        self.last_profiles_version = 0

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

    def enqueue_registration_sample(
        self,
        *,
        sample_id: str,
        session_id: str,
        pose: str,
        quality: float,
        face_crop: np.ndarray,
        embeddings: dict[str, list[np.ndarray]],
    ) -> str | None:
        if face_crop is None or getattr(face_crop, "size", 0) == 0:
            return None
        success, encoded = cv2.imencode(".jpg", face_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not success:
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
            return None
        return self.outbound_queue.enqueue("registration_sample", payload)

