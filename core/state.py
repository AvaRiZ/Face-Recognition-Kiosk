from __future__ import annotations

import time
import threading
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
        self._tracking_refresh_event = threading.Event()
        self._reset_registration_collections()
        self._registration_state.phase = "idle"

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

    def _registration_samples_flattened(self) -> list[RegistrationSample]:
        samples: list[RegistrationSample] = []
        for pose in self._registration_state.required_poses:
            samples.extend(self._registration_state.samples_by_pose.get(pose, []))
        return samples

    def _reset_registration_collections(self) -> None:
        state = self._registration_state
        state.current_pose_index = 0
        state.samples_by_pose = {pose: [] for pose in state.required_poses}
        state.pose_capture_counts = {pose: 0 for pose in state.required_poses}
        state._max_captures_override = None
        state.capture_track_id = None
        state.selected_track_id = None

    def _session_timeout_seconds(self) -> int:
        return int(getattr(self._config, "registration_session_timeout_seconds", 0) or 0)

    def _set_session_expiry(self, *, now: float | None = None) -> None:
        state = self._registration_state
        timeout = self._session_timeout_seconds()
        if timeout <= 0:
            state.expires_at = None
            return
        observed_now = float(now if now is not None else time.time())
        state.expires_at = observed_now + timeout

    def _begin_registration_session(
        self,
        *,
        registration_kind: str = "student",
        reason_code: str = "session_started",
        reason_message: str = "Registration session started. Keep one unregistered student in frame.",
    ) -> str:
        state = self._registration_state
        now = time.time()
        session_id = uuid.uuid4().hex
        state.session_id = session_id
        state.phase = "capturing"
        state.registration_kind = registration_kind
        state.force_new_identity = False
        state.session_started_at = now
        state.last_activity_at = now
        self._set_session_expiry(now=now)
        self.set_registration_status_reason(reason_code, reason_message)
        return session_id

    def _clear_registration_session(self, *, preserve_phase: bool = False) -> None:
        state = self._registration_state
        state.session_id = None
        state.session_started_at = None
        state.last_activity_at = None
        state.expires_at = None
        state.force_new_identity = False
        state.capture_track_id = None
        state.selected_track_id = None
        if not preserve_phase:
            state.phase = "idle"

    def _touch_registration_session(self) -> None:
        state = self._registration_state
        if state.phase not in {"capturing", "ready"}:
            return
        now = time.time()
        state.last_activity_at = now
        self._set_session_expiry(now=now)

    def _refresh_tracking_after_registration_boundary(self) -> None:
        # Defer tracker reset to the recognition loop thread to avoid cross-thread dict mutation.
        self._tracking_refresh_event.set()
        self._recognized_user = None

    def expire_registration_session_if_needed(self) -> bool:
        state = self._registration_state
        if state.phase not in {"capturing", "ready"}:
            return False
        if state.expires_at is None:
            return False
        if time.time() <= float(state.expires_at):
            return False
        self._reset_registration_collections()
        self._clear_registration_session(preserve_phase=True)
        state.phase = "expired"
        state.expires_at = time.time()
        self._refresh_tracking_after_registration_boundary()
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
            return bool(self._registration_state.ready_to_submit)
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
        if state.phase != "capturing":
            return False
        if state.current_pose is None:
            return False
        state.current_pose_index += 1
        if state.current_pose_index >= len(state.required_poses):
            state.current_pose_index = len(state.required_poses)
            state.phase = "ready"
            self.set_registration_status_reason(
                "capture_complete",
                "Required registration captures are complete. Enter student details to submit.",
            )
            self._touch_registration_session()
            return False
        self._touch_registration_session()
        return True

    def is_registration_ready(self) -> bool:
        return bool(self._registration_state.ready_to_submit)

    def get_registration_expires_in_seconds(self) -> int | None:
        expires_at = self._registration_state.expires_at
        if expires_at is None:
            return None
        return max(0, int(float(expires_at) - time.time()))

    def get_registration_control(self) -> dict[str, object]:
        state = self._registration_state
        return {
            "session_id": state.session_id,
            "phase": state.phase,
            "expected_pose": state.current_pose,
            "force_new_identity": bool(state.force_new_identity),
            "registration_kind": state.registration_kind,
        }

    def get_registration_progress(self) -> dict[str, object]:
        state = self._registration_state
        poses_progress: dict[str, dict[str, object]] = {}
        required_total = state.total_required_captures
        captured_total = state.capture_count

        for idx, pose in enumerate(state.required_poses):
            captured = int(state.pose_capture_counts.get(pose, 0))
            retained = len(state.samples_by_pose.get(pose, []))
            completed = idx < state.current_pose_index or state.phase == "ready"
            poses_progress[pose] = {
                "captured": captured,
                "required": state.samples_per_pose_target,
                "retained": retained,
                "retained_target": state.retained_samples_per_pose,
                "completed": completed,
            }

        return {
            "phase": state.phase,
            "session_id": state.session_id,
            "registration_kind": state.registration_kind,
            "force_new_identity": bool(state.force_new_identity),
            "required_poses": list(state.required_poses),
            "current_pose": state.current_pose,
            "current_pose_index": int(state.current_pose_index),
            "pose_progress": poses_progress,
            "total_progress": {
                "captured": captured_total,
                "required": required_total,
                "retained": len(self._registration_samples_flattened()),
                "retained_required": state.total_retained_samples,
            },
            "ready_to_submit": self.is_registration_ready(),
            "expires_in_seconds": self.get_registration_expires_in_seconds(),
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
        self._config.base_threshold = float(threshold)
        self._config.face_quality_threshold = float(quality_threshold)

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

    def start_registration_session(self, registration_kind: str = "student") -> bool:
        state = self._registration_state
        if state.phase in {"capturing", "ready"}:
            return False
        normalized_kind = str(registration_kind or "student").strip().lower()
        if normalized_kind not in {"student", "visitor", "unrecognized"}:
            normalized_kind = "student"
        self._reset_registration_collections()
        self._begin_registration_session(
            registration_kind=normalized_kind,
            reason_code="session_started",
            reason_message="Registration session started. Keep one unregistered student in frame.",
        )
        self._refresh_tracking_after_registration_boundary()
        return True

    def cancel_registration_session(
        self,
        *,
        reason_code: str = "session_canceled",
        reason_message: str = "Registration session canceled.",
    ) -> None:
        self._reset_registration_collections()
        self._clear_registration_session(preserve_phase=False)
        self._registration_state.registration_kind = "student"
        self._refresh_tracking_after_registration_boundary()
        self.set_registration_status_reason(reason_code, reason_message)

    def override_registration_session(self) -> bool:
        state = self._registration_state
        if state.phase not in {"capturing", "ready"}:
            return False
        state.force_new_identity = True
        self._touch_registration_session()
        self.set_registration_status_reason(
            "override_forced_unknown",
            "Manual override enabled. Continuing registration as an unknown student.",
        )
        return True

    def sync_registration_control(
        self,
        *,
        session_id: str | None,
        phase: str,
        expected_pose: str | None = None,
        force_new_identity: bool = False,
        registration_kind: str = "student",
        pose_capture_counts: dict[str, int] | None = None,
    ) -> None:
        state = self._registration_state
        previous_phase = str(state.phase or "idle").strip().lower()
        previous_session_id = str(state.session_id or "").strip() or None
        next_session_id = str(session_id or "").strip() or None
        session_changed = previous_session_id != next_session_id
        previous_pose_index = int(state.current_pose_index or 0)
        previous_pose_counts = {
            pose: int(state.pose_capture_counts.get(pose, 0) or 0)
            for pose in state.required_poses
        }
        normalized_phase = str(phase or "idle").strip().lower()
        if normalized_phase not in {"idle", "capturing", "ready", "expired"}:
            normalized_phase = "idle"
        preserve_local_capture_progress = bool(
            not session_changed
            and normalized_phase == "capturing"
            and previous_phase in {"capturing", "ready"}
        )
        previous_active = previous_phase in {"capturing", "ready"}
        next_active = normalized_phase in {"capturing", "ready"}
        registration_lifecycle_changed = session_changed or (previous_active != next_active)
        state.phase = "ready" if preserve_local_capture_progress and previous_phase == "ready" else normalized_phase
        state.session_id = next_session_id
        state.force_new_identity = bool(force_new_identity)
        normalized_kind = str(registration_kind or "student").strip().lower()
        if normalized_kind not in {"student", "visitor", "unrecognized"}:
            normalized_kind = "student"
        state.registration_kind = normalized_kind

        if state.phase != "capturing":
            state.capture_track_id = None
            state.selected_track_id = None

        if state.phase in {"idle", "expired"}:
            self._reset_registration_collections()
        elif session_changed:
            self._reset_registration_collections()

        if registration_lifecycle_changed:
            self._refresh_tracking_after_registration_boundary()

        if state.phase == "capturing":
            pose = str(expected_pose or "").strip().lower()
            if pose in state.required_poses:
                next_pose_index = state.required_poses.index(pose)
                if preserve_local_capture_progress:
                    next_pose_index = max(previous_pose_index, next_pose_index)
                state.current_pose_index = next_pose_index
            elif state.current_pose_index >= len(state.required_poses):
                state.current_pose_index = 0

        if pose_capture_counts is not None:
            synced_counts: dict[str, int] = {}
            for pose in state.required_poses:
                incoming_count = max(0, int(pose_capture_counts.get(pose, 0) or 0))
                if preserve_local_capture_progress:
                    incoming_count = max(previous_pose_counts.get(pose, 0), incoming_count)
                synced_counts[pose] = incoming_count
            state.pose_capture_counts = synced_counts

    def reset_registration_state(self) -> None:
        self._recognized_user = None
        self.cancel_registration_session(
            reason_code="session_reset",
            reason_message="Registration session was reset.",
        )

    def reset_database_state(self) -> None:
        self.reset_registration_state()
        self._users = []
        self._tracked_faces = {}
        self._face_stability = {}

    def capture_registration_sample(self, sample: RegistrationSample) -> int:
        state = self._registration_state
        if state.phase != "capturing":
            return state.capture_count
        current_pose = self.get_current_registration_pose()
        if current_pose is None:
            return state.capture_count

        sample_pose = str(getattr(sample, "pose", "") or "").strip().lower()
        if sample_pose and sample_pose != current_pose:
            return state.capture_count

        sample.pose = current_pose
        pose_samples = state.samples_by_pose.setdefault(current_pose, [])
        pose_samples.append(sample)
        state.pose_capture_counts[current_pose] = state.pose_capture_counts.get(current_pose, 0) + 1

        if self.current_pose_has_enough_samples():
            self.select_top_samples_for_pose(current_pose)
            self.advance_registration_pose()
        self._touch_registration_session()
        return state.capture_count

    def clear_captured_samples(self) -> bool:
        state = self._registration_state
        if state.phase not in {"capturing", "ready"}:
            return False
        self._reset_registration_collections()
        state.phase = "capturing"
        self._touch_registration_session()
        self.set_registration_status_reason(
            "samples_cleared",
            "Captured registration samples were cleared.",
        )
        return True

    def complete_registration(self) -> None:
        self.cancel_registration_session(
            reason_code="registration_submitted",
            reason_message="Registration submitted successfully.",
        )

    def request_manual_registration(self) -> None:
        if self._registration_state.phase not in {"capturing", "ready"}:
            self.start_registration_session("student")
        self.set_registration_status_reason(
            "session_requested",
            "Registration session requested. Waiting to lock onto an unregistered student.",
        )

    def start_manual_registration(self, track_id: int) -> None:
        state = self._registration_state
        if state.phase != "capturing":
            state.phase = "capturing"
        state.capture_track_id = int(track_id)
        state.selected_track_id = int(track_id)
        self._touch_registration_session()
        self.set_registration_status_reason(
            "capture_locked",
            "Student face locked. Capturing required pose samples.",
        )

    def stop_manual_registration(self) -> None:
        state = self._registration_state
        state.capture_track_id = None
        state.selected_track_id = None
        if state.session_id is None and state.phase == "capturing":
            state.phase = "idle"

    def start_web_registration_session(self) -> bool:
        return self.start_registration_session("student")

    def cancel_web_registration_session(self) -> None:
        self.cancel_registration_session(
            reason_code="session_canceled",
            reason_message="Registration session canceled.",
        )

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
        track_state.unrecognized_event_id = None
        track_state.unrecognized_logged = False
        track_state.unrecognized_revoked = False
        track_state.unrecognized_first_seen = 0.0
        track_state.unrecognized_face_quality = None
        track_state.unrecognized_confidence = None
        track_state.unrecognized_threshold = None
        track_state.last_label = "Tracking"
        track_state.last_label_color = (180, 180, 180)
        track_state.selected_for_registration = False
        track_state.registration_recognized_streak = 0
        track_state.registration_recognized_name = ""
        self._face_stability.pop(track_id, None)
        return track_state

    def enable_unknown_registration_override(self) -> None:
        if not self.override_registration_session():
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

    def consume_tracking_refresh_request(self) -> bool:
        if not self._tracking_refresh_event.is_set():
            return False
        self._tracking_refresh_event.clear()
        self.clear_tracking_state()
        return True

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
