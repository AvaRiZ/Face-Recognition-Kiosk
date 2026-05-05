from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.config import AppConfig
from core.models import (
    EmbeddingMap,
    FaceStabilityState,
    RegistrationSample,
    RegistrationState,
    TrackingState,
    User,
)

_REGISTRATION_FLAG_UNSET = object()


class AppStateManager:
    def __init__(self, config: AppConfig):
        self._config = config
        self._users: list[User] = []
        self._recognized_user: Optional[dict[str, str]] = None
        self._registration_state = RegistrationState(
            samples_per_pose_target=int(config.registration_samples_per_pose_target),
            retained_samples_per_pose=int(config.registration_retained_samples_per_pose),
        )
        self._tracked_faces: dict[int, TrackingState] = {}
        self._face_stability: dict[int, FaceStabilityState] = {}

        self._base_threshold = float(config.base_threshold)
        self._face_quality_threshold = float(config.face_quality_threshold)
        self._worker_heartbeats: dict[str, dict[str, object]] = {}
        self._registration_ingested_sample_ids_by_session: dict[str, set[str]] = {}
        self._reset_registration_collections()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def set_registration_status_reason(self, code: str | None, message: str = "", updated_at: str | None = None) -> None:
        state = self._registration_state
        normalized_code = (code or "").strip()
        state.status_reason_code = normalized_code or None
        state.status_reason_message = (message or "").strip()
        state.status_updated_at = (updated_at or self._utc_now_iso()) if state.status_reason_code else None

    def clear_registration_status_reason(self) -> None:
        self.set_registration_status_reason(None, "", updated_at=None)

    def _refresh_registration_limits(self) -> None:
        self._registration_state.max_captures = self._registration_state.total_required_captures

    def _registration_samples_flattened(self) -> list[RegistrationSample]:
        samples: list[RegistrationSample] = []
        for pose in self._registration_state.required_poses:
            samples.extend(self._registration_state.samples_by_pose.get(pose, []))
        return samples

    def _sync_flat_captured_samples(self) -> None:
        self._registration_state.captured_samples = self._registration_samples_flattened()

    def _reset_registration_collections(self) -> None:
        state = self._registration_state
        state.pending_registration = None
        state.in_progress = False
        state.current_pose_index = 0
        state.samples_by_pose = {pose: [] for pose in state.required_poses}
        state.pose_capture_counts = {pose: 0 for pose in state.required_poses}
        state.captured_samples = []
        self._refresh_registration_limits()

    def _begin_registration_session(self) -> str:
        session_id = uuid.uuid4().hex
        self._registration_state.session_id = session_id
        self._registration_ingested_sample_ids_by_session = {session_id: set()}
        return session_id

    def _clear_registration_session(self) -> None:
        self._registration_state.session_id = None
        self._registration_ingested_sample_ids_by_session = {}

    def _mark_registration_session_started(self) -> None:
        now = time.time()
        state = self._registration_state
        state.session_started_at = now
        state.last_activity_at = now
        state.session_expired = False

    def _touch_registration_session(self) -> None:
        self._registration_state.last_activity_at = time.time()

    def _clear_registration_session_timestamps(self) -> None:
        state = self._registration_state
        state.session_started_at = None
        state.last_activity_at = None

    def _set_registration_flags(
        self,
        *,
        capture_requested: bool | None = None,
        capture_active: bool | None = None,
        capture_track_id: Optional[int] | object = _REGISTRATION_FLAG_UNSET,
        selected_track_id: Optional[int] | object = _REGISTRATION_FLAG_UNSET,
        session_active: bool | None = None,
        session_expired: bool | None = None,
        allow_unknown_override: bool | None = None,
    ) -> None:
        state = self._registration_state
        if capture_requested is not None:
            state.capture_requested = capture_requested
        if capture_active is not None:
            state.capture_active = capture_active
        if capture_track_id is not _REGISTRATION_FLAG_UNSET:
            state.capture_track_id = capture_track_id  # type: ignore[assignment]
        if selected_track_id is not _REGISTRATION_FLAG_UNSET:
            state.selected_track_id = selected_track_id  # type: ignore[assignment]
        if session_active is not None:
            state.session_active = session_active
        if session_expired is not None:
            state.session_expired = session_expired
        if allow_unknown_override is not None:
            state.allow_unknown_override = allow_unknown_override

    def _reset_registration_session_state(self) -> None:
        self._clear_registration_session()
        self._clear_registration_session_timestamps()

    def _restart_registration_session(self, *, create_session_id: bool) -> None:
        if create_session_id:
            self._begin_registration_session()
        else:
            self._clear_registration_session()
        self._mark_registration_session_started()

    def expire_registration_session_if_needed(self) -> bool:
        timeout_seconds = int(getattr(self._config, "registration_session_timeout_seconds", 0))
        if timeout_seconds <= 0:
            return False

        state = self._registration_state
        has_session_state = bool(
            state.session_active
            or state.capture_requested
            or state.capture_active
            or state.in_progress
            or state.pending_registration
            or state.capture_count > 0
        )
        if not has_session_state:
            return False

        last_activity = state.last_activity_at or state.session_started_at
        if last_activity is None:
            self._mark_registration_session_started()
            return False

        if (time.time() - float(last_activity)) <= timeout_seconds:
            return False

        self._set_registration_flags(
            capture_requested=False,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            session_expired=True,
            allow_unknown_override=False,
        )
        self._reset_registration_collections()
        self._reset_registration_session_state()
        self.set_registration_status_reason(
            "session_expired",
            "Registration session expired due to inactivity. Start a new session to continue.",
        )
        return True

    def get_current_registration_pose(self) -> Optional[str]:
        return self._registration_state.current_pose

    def get_pose_capture_count(self, pose: Optional[str] = None) -> int:
        target_pose = pose or self.get_current_registration_pose()
        if target_pose is None:
            return 0
        return int(self._registration_state.pose_capture_counts.get(target_pose, 0))

    def current_pose_has_enough_samples(self) -> bool:
        current_pose = self.get_current_registration_pose()
        if current_pose is None:
            return bool(self._registration_state.in_progress and self._registration_state.pending_registration)
        return self.get_pose_capture_count(current_pose) >= self._registration_state.samples_per_pose_target

    def select_top_samples_for_pose(self, pose: str) -> list[RegistrationSample]:
        pose_samples = list(self._registration_state.samples_by_pose.get(pose, []))
        if not pose_samples:
            self._registration_state.samples_by_pose[pose] = []
            return []

        pose_samples.sort(key=lambda sample: float(sample.quality), reverse=True)
        retained = pose_samples[: self._registration_state.retained_samples_per_pose]
        self._registration_state.samples_by_pose[pose] = retained
        return retained

    def advance_registration_pose(self) -> bool:
        state = self._registration_state
        if state.current_pose is None:
            return False
        state.current_pose_index += 1
        if state.current_pose_index >= len(state.required_poses):
            final_samples = self._registration_samples_flattened()
            state.pending_registration = list(final_samples)
            state.in_progress = True
            self._sync_flat_captured_samples()
            self.set_registration_status_reason(
                "capture_complete",
                "Required registration captures are complete. Enter student details to submit.",
            )
            return False
        self._sync_flat_captured_samples()
        return True

    def is_registration_ready(self) -> bool:
        state = self._registration_state
        return bool(state.in_progress and state.pending_registration)

    def get_registration_progress(self) -> dict[str, object]:
        state = self._registration_state
        poses_progress: dict[str, dict[str, object]] = {}
        required_total = state.total_required_captures
        captured_total = state.capture_count

        for idx, pose in enumerate(state.required_poses):
            captured = int(state.pose_capture_counts.get(pose, 0))
            retained = len(state.samples_by_pose.get(pose, []))
            completed = idx < state.current_pose_index or (
                idx == state.current_pose_index and state.current_pose is None and state.in_progress
            )
            poses_progress[pose] = {
                "captured": captured,
                "required": state.samples_per_pose_target,
                "retained": retained,
                "retained_target": state.retained_samples_per_pose,
                "completed": completed,
            }

        return {
            "required_poses": list(state.required_poses),
            "current_pose": state.current_pose,
            "current_pose_index": int(state.current_pose_index),
            "pose_progress": poses_progress,
            "total_progress": {
                "captured": captured_total,
                "required": required_total,
                "retained": len(state.pending_registration or self._registration_samples_flattened()),
                "retained_required": state.total_retained_samples,
            },
            "ready_to_submit": self.is_registration_ready(),
        }

    @property
    def users(self) -> list[User]:
        return self._users

    @property
    def user_count(self) -> int:
        return len(self._users)

    @property
    def recognized_user(self) -> Optional[dict[str, str]]:
        return self._recognized_user

    @property
    def registration_state(self) -> RegistrationState:
        return self._registration_state

    @property
    def tracked_faces(self) -> dict[int, TrackingState]:
        return self._tracked_faces

    @property
    def face_stability(self) -> dict[int, FaceStabilityState]:
        return self._face_stability

    @property
    def base_threshold(self) -> float:
        return self._base_threshold

    @property
    def face_quality_threshold(self) -> float:
        return self._face_quality_threshold

    def get_thresholds(self) -> tuple[float, float]:
        return self._base_threshold, self._face_quality_threshold

    def set_thresholds(self, threshold: float, quality_threshold: float) -> None:
        self._base_threshold = float(threshold)
        self._face_quality_threshold = float(quality_threshold)

    def load_users(self, users: list[User]) -> None:
        self._users = list(users)

    def append_user(self, user: User) -> None:
        self._users.append(user)

    def remove_user(self, user_id: int) -> None:
        self._users = [user for user in self._users if user.id != user_id]

    def replace_user(self, updated_user: User) -> None:
        for idx, user in enumerate(self._users):
            if user.id == updated_user.id:
                self._users[idx] = updated_user
                return
        self._users.append(updated_user)

    def find_user_index(self, user_id: int) -> int:
        for idx, user in enumerate(self._users):
            if user.id == user_id:
                return idx
        return -1

    def set_recognized_user(self, payload: Optional[dict[str, str]]) -> None:
        self._recognized_user = payload

    def reset_registration_state(self) -> None:
        self._recognized_user = None
        self._reset_registration_collections()
        self._set_registration_flags(
            capture_requested=False,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_session_state()
        self.set_registration_status_reason(
            "session_reset",
            "Registration session was reset.",
        )

    def reset_database_state(self) -> None:
        self.reset_registration_state()
        self._users = []
        self._tracked_faces = {}
        self._face_stability = {}

    def capture_registration_sample(self, sample: RegistrationSample) -> int:
        state = self._registration_state
        if state.in_progress:
            return state.capture_count

        current_pose = self.get_current_registration_pose()
        if current_pose is None:
            return state.capture_count

        sample.pose = current_pose
        pose_samples = state.samples_by_pose.setdefault(current_pose, [])
        pose_samples.append(sample)
        state.pose_capture_counts[current_pose] = state.pose_capture_counts.get(current_pose, 0) + 1

        if self.current_pose_has_enough_samples():
            self.select_top_samples_for_pose(current_pose)
            self.advance_registration_pose()

        self._sync_flat_captured_samples()
        self._touch_registration_session()
        return state.capture_count

    def clear_captured_samples(self) -> None:
        self._reset_registration_collections()
        self.set_registration_status_reason(
            "samples_cleared",
            "Captured registration samples were cleared.",
        )

    def complete_registration(self) -> None:
        self._reset_registration_collections()
        self._set_registration_flags(
            capture_requested=False,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_session_state()
        self.set_registration_status_reason(
            "registration_submitted",
            "Registration submitted successfully.",
        )

    def request_manual_registration(self) -> None:
        self._set_registration_flags(
            capture_requested=True,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_collections()
        self._restart_registration_session(create_session_id=False)
        self.set_registration_status_reason(
            "session_requested",
            "Registration session requested. Waiting to lock onto an unregistered student.",
        )

    def start_manual_registration(self, track_id: int) -> None:
        self._set_registration_flags(
            capture_requested=False,
            capture_active=True,
            capture_track_id=track_id,
            selected_track_id=track_id,
            session_active=False,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_collections()
        self._restart_registration_session(create_session_id=False)
        self.set_registration_status_reason(
            "capture_locked",
            "Student face locked. Capturing required pose samples.",
        )

    def stop_manual_registration(self) -> None:
        self._set_registration_flags(
            capture_requested=False,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            allow_unknown_override=False,
        )
        # Keep finalized pending samples intact so web registration can continue.
        if not self.is_registration_ready():
            self._reset_registration_collections()
            self._reset_registration_session_state()

    def start_web_registration_session(self) -> bool:
        state = self._registration_state
        if state.in_progress or state.capture_active or state.capture_requested or state.session_active:
            return False
        self._set_registration_flags(
            capture_requested=True,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=True,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_collections()
        self._restart_registration_session(create_session_id=True)
        self.set_registration_status_reason(
            "session_started",
            "Registration session started. Keep one unregistered student in frame.",
        )
        return True

    def cancel_web_registration_session(self) -> None:
        self._set_registration_flags(
            capture_requested=False,
            capture_active=False,
            capture_track_id=None,
            selected_track_id=None,
            session_active=False,
            session_expired=False,
            allow_unknown_override=False,
        )
        self._reset_registration_collections()
        self._reset_registration_session_state()
        self.set_registration_status_reason(
            "session_canceled",
            "Registration session canceled.",
        )

    def claim_registration_sample_id(self, session_id: str, sample_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        normalized_sample_id = str(sample_id or "").strip()
        if not normalized_session_id or not normalized_sample_id:
            return False
        seen = self._registration_ingested_sample_ids_by_session.setdefault(normalized_session_id, set())
        if normalized_sample_id in seen:
            return False
        seen.add(normalized_sample_id)
        return True

    def record_worker_heartbeat(
        self,
        *,
        worker_role: str,
        station_id: str | None = None,
        camera_id: int | None = None,
        observed_at: float | None = None,
    ) -> float:
        normalized_role = str(worker_role or "").strip().lower()
        if normalized_role not in {"entry", "exit"}:
            normalized_role = "entry"
        timestamp = float(observed_at if observed_at is not None else time.time())
        self._worker_heartbeats[normalized_role] = {
            "last_seen_at": timestamp,
            "station_id": str(station_id or "").strip() or None,
            "camera_id": int(camera_id) if camera_id is not None else None,
        }
        return timestamp

    def get_worker_last_seen_at(self, worker_role: str) -> float | None:
        normalized_role = str(worker_role or "").strip().lower()
        payload = self._worker_heartbeats.get(normalized_role) or {}
        value = payload.get("last_seen_at")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def is_worker_online(self, worker_role: str, ttl_seconds: int) -> bool:
        ttl = int(ttl_seconds or 0)
        if ttl <= 0:
            return False
        last_seen = self.get_worker_last_seen_at(worker_role)
        if last_seen is None:
            return False
        return (time.time() - last_seen) <= ttl

    def initialize_track_state(self, track_id: int, current_time: float) -> TrackingState:
        if track_id not in self._tracked_faces:
            self._tracked_faces[track_id] = TrackingState(last_seen=current_time)
        else:
            self._tracked_faces[track_id].last_seen = current_time
        return self._tracked_faces[track_id]

    def reset_track_identity(self, track_id: int) -> Optional[TrackingState]:
        track_state = self._tracked_faces.get(track_id)
        if track_state is None:
            return None
        track_state.recognized = False
        track_state.user = None
        track_state.last_recognition_time = 0.0
        track_state.last_recognition_confidence = None
        track_state.last_recognition_threshold = None
        track_state.failed_good_quality_attempts = 0
        track_state.last_label = "Tracking"
        track_state.last_label_color = (180, 180, 180)
        track_state.selected_for_registration = False
        track_state.registration_recognized_streak = 0
        track_state.registration_recognized_name = ""
        self._face_stability.pop(track_id, None)
        return track_state

    def enable_unknown_registration_override(self) -> None:
        self._registration_state.allow_unknown_override = True
        self._touch_registration_session()

    def get_track_state(self, track_id: int) -> Optional[TrackingState]:
        return self._tracked_faces.get(track_id)

    def remove_track_state(self, track_id: int) -> None:
        self._tracked_faces.pop(track_id, None)
        self._face_stability.pop(track_id, None)

    def clear_tracking_state(self) -> None:
        self._tracked_faces.clear()
        self._face_stability.clear()

    def get_or_create_face_stability(self, face_id: int) -> FaceStabilityState:
        if face_id not in self._face_stability:
            self._face_stability[face_id] = FaceStabilityState()
        return self._face_stability[face_id]

    def set_user_embeddings(self, user_id: int, embeddings: EmbeddingMap, image_paths: list[str], embedding_dim: int) -> None:
        idx = self.find_user_index(user_id)
        if idx == -1:
            return
        user = self._users[idx]
        user.embeddings = embeddings
        user.image_paths = image_paths
        user.embedding_dim = embedding_dim
