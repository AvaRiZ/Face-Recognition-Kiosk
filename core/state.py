from __future__ import annotations

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
        self._users: list[User] = []
        self._recognized_user: Optional[dict[str, str]] = None
        self._registration_state = RegistrationState(max_captures=10)
        self._tracked_faces: dict[int, TrackingState] = {}
        self._face_stability: dict[int, FaceStabilityState] = {}

        self._base_threshold = float(config.base_threshold)
        self._face_quality_threshold = float(config.face_quality_threshold)

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
        self._registration_state.pending_registration = None
        self._registration_state.in_progress = False
        self._registration_state.captured_samples = []
        self._registration_state.manual_requested = False
        self._registration_state.manual_active = False
        self._registration_state.manual_track_id = None

    def reset_database_state(self) -> None:
        self.reset_registration_state()
        self._users = []
        self._tracked_faces = {}
        self._face_stability = {}

    def capture_registration_sample(self, sample: RegistrationSample) -> int:
        if self._registration_state.in_progress:
            return self._registration_state.capture_count
        if self._registration_state.capture_count >= self._registration_state.max_captures:
            return self._registration_state.capture_count

        self._registration_state.captured_samples.append(sample)
        if self._registration_state.capture_count >= self._registration_state.max_captures:
            self._registration_state.pending_registration = list(self._registration_state.captured_samples)
            self._registration_state.in_progress = True
        return self._registration_state.capture_count

    def clear_captured_samples(self) -> None:
        self._registration_state.captured_samples = []

    def complete_registration(self) -> None:
        self._registration_state.pending_registration = None
        self._registration_state.in_progress = False
        self._registration_state.captured_samples = []
        self._registration_state.manual_requested = False
        self._registration_state.manual_active = False
        self._registration_state.manual_track_id = None

    def request_manual_registration(self) -> None:
        self._registration_state.manual_requested = True
        self._registration_state.manual_active = False
        self._registration_state.manual_track_id = None
        self._registration_state.captured_samples = []

    def start_manual_registration(self, track_id: int) -> None:
        self._registration_state.manual_requested = False
        self._registration_state.manual_active = True
        self._registration_state.manual_track_id = track_id
        self._registration_state.captured_samples = []

    def stop_manual_registration(self) -> None:
        self._registration_state.manual_requested = False
        self._registration_state.manual_active = False
        self._registration_state.manual_track_id = None
        self._registration_state.captured_samples = []

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
        self._face_stability.pop(track_id, None)
        return track_state

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
