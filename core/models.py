from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

EmbeddingMap = dict[str, list[np.ndarray]]


@dataclass
class EmbeddingSet:
    by_model: EmbeddingMap = field(default_factory=dict)


@dataclass
class User:
    id: int
    name: str
    sr_code: str
    gender: str = ""
    program: str = ""
    user_type: str = "enrolled"
    embeddings: EmbeddingMap = field(default_factory=dict)
    image_paths: list[str] = field(default_factory=list)
    embedding_dim: int = 0


@dataclass
class RecognitionResult:
    user_id: int
    confidence: float
    primary_confidence: float
    secondary_confidence: float
    distance: float
    primary_distance: float
    secondary_distance: float
    threshold: float
    user: User
    user_index: int


@dataclass
class RegistrationSample:
    face_crop: np.ndarray
    embeddings: EmbeddingMap
    quality: float
    pose: str = "front"


@dataclass
class RegistrationState:
    session_id: Optional[str] = None
    phase: str = "idle"  # idle | capturing | ready | expired
    registration_kind: str = "student"
    force_new_identity: bool = False
    required_poses: list[str] = field(default_factory=lambda: ["front", "left", "right"])
    current_pose_index: int = 0
    samples_by_pose: dict[str, list[RegistrationSample]] = field(default_factory=dict)
    pose_capture_counts: dict[str, int] = field(default_factory=dict)
    samples_per_pose_target: int = 10
    retained_samples_per_pose: int = 5
    capture_track_id: Optional[int] = None
    selected_track_id: Optional[int] = None
    session_started_at: Optional[float] = None
    expires_at: Optional[float] = None
    last_activity_at: Optional[float] = None
    status_reason_code: Optional[str] = None
    status_reason_message: str = ""
    status_updated_at: Optional[str] = None
    _max_captures_override: Optional[int] = None

    def _flattened_samples(self) -> list[RegistrationSample]:
        samples: list[RegistrationSample] = []
        for pose in self.required_poses:
            samples.extend(self.samples_by_pose.get(pose, []))
        return samples

    @property
    def capture_count(self) -> int:
        return int(sum(self.pose_capture_counts.values()))

    @property
    def captured_samples(self) -> list[RegistrationSample]:
        return self._flattened_samples()

    @captured_samples.setter
    def captured_samples(self, value: list[RegistrationSample]) -> None:
        samples = list(value or [])
        if not self.required_poses:
            self.samples_by_pose = {}
            self.pose_capture_counts = {}
            return
        default_pose = self.required_poses[0]
        self.samples_by_pose = {pose: [] for pose in self.required_poses}
        for sample in samples:
            pose = str(getattr(sample, "pose", "") or "").strip().lower()
            if pose not in self.samples_by_pose:
                pose = default_pose
            self.samples_by_pose[pose].append(sample)
        self.pose_capture_counts = {
            pose: len(self.samples_by_pose.get(pose, []))
            for pose in self.required_poses
        }

    @property
    def pending_registration(self) -> Optional[list[RegistrationSample]]:
        if self.phase != "ready":
            return None
        return self._flattened_samples()

    @pending_registration.setter
    def pending_registration(self, value: Optional[list[RegistrationSample]]) -> None:
        if value:
            self.captured_samples = list(value)
            self.phase = "ready"
        else:
            self.captured_samples = []
            if self.phase == "ready":
                self.phase = "idle"

    @property
    def current_pose(self) -> Optional[str]:
        if not self.required_poses:
            return None
        if self.phase != "capturing":
            return None
        if self.current_pose_index >= len(self.required_poses):
            return None
        return self.required_poses[self.current_pose_index]

    @property
    def total_required_captures(self) -> int:
        return len(self.required_poses) * int(self.samples_per_pose_target)

    @property
    def total_retained_samples(self) -> int:
        return len(self.required_poses) * int(self.retained_samples_per_pose)

    @property
    def max_captures(self) -> int:
        if self._max_captures_override is not None:
            return int(self._max_captures_override)
        return self.total_required_captures

    @max_captures.setter
    def max_captures(self, value: int) -> None:
        self._max_captures_override = int(value)

    @property
    def ready_to_submit(self) -> bool:
        return bool(
            self.phase == "ready"
            and len(self._flattened_samples()) >= int(self.total_retained_samples)
        )

    # Backward-compatible aliases used by existing route/frontend payloads.
    @property
    def manual_requested(self) -> bool:
        return self.phase == "capturing" and self.capture_track_id is None

    @manual_requested.setter
    def manual_requested(self, value: bool) -> None:
        if value:
            self.phase = "capturing"
            self.capture_track_id = None
        elif self.phase == "capturing" and self.capture_track_id is None:
            self.phase = "idle"

    @property
    def manual_active(self) -> bool:
        return self.phase == "capturing" and self.capture_track_id is not None

    @manual_active.setter
    def manual_active(self, value: bool) -> None:
        if value:
            self.phase = "capturing"
            if self.capture_track_id is None:
                self.capture_track_id = self.selected_track_id
        else:
            self.capture_track_id = None
            if self.phase == "capturing":
                self.phase = "idle"

    @property
    def manual_track_id(self) -> Optional[int]:
        return self.capture_track_id

    @manual_track_id.setter
    def manual_track_id(self, value: Optional[int]) -> None:
        self.capture_track_id = value

    @property
    def web_session_active(self) -> bool:
        return self.phase == "capturing"

    @web_session_active.setter
    def web_session_active(self, value: bool) -> None:
        if value:
            self.phase = "capturing"
        elif self.phase == "capturing":
            self.phase = "idle"

    @property
    def session_active(self) -> bool:
        return self.phase == "capturing"

    @session_active.setter
    def session_active(self, value: bool) -> None:
        self.web_session_active = value

    @property
    def session_expired(self) -> bool:
        return self.phase == "expired"

    @session_expired.setter
    def session_expired(self, value: bool) -> None:
        if value:
            self.phase = "expired"
        elif self.phase == "expired":
            self.phase = "idle"

    @property
    def allow_unknown_override(self) -> bool:
        return bool(self.force_new_identity)

    @allow_unknown_override.setter
    def allow_unknown_override(self, value: bool) -> None:
        self.force_new_identity = bool(value)

    @property
    def in_progress(self) -> bool:
        return self.phase == "ready"

    @in_progress.setter
    def in_progress(self, value: bool) -> None:
        if value:
            self.phase = "ready"
        elif self.phase == "ready":
            self.phase = "idle"


@dataclass
class FaceStabilityState:
    positions: list[tuple[float, float]] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    stable_since: Optional[float] = None


@dataclass
class TrackingState:
    recognized: bool = False
    user: Optional[dict[str, str]] = None
    last_seen: float = 0.0
    last_recognition_time: float = 0.0
    last_bbox: Optional[tuple[int, int, int, int]] = None
    last_detection_confidence: Optional[float] = None
    last_quality_score: float = 0.0
    last_quality_status: str = "Poor"
    last_quality_debug: dict = field(default_factory=dict)
    last_registration_quality_score: float = 0.0
    last_registration_quality_status: str = "Poor"
    last_registration_quality_debug: dict = field(default_factory=dict)
    last_landmarks: Optional[dict] = None
    last_pose: Optional[str] = None
    last_stable: bool = False
    last_area: int = 0
    last_analysis_frame_index: int = -1
    last_label: str = "Tracking"
    last_label_color: tuple[int, int, int] = (180, 180, 180)
    last_recognition_confidence: Optional[float] = None
    last_recognition_threshold: Optional[float] = None
    failed_good_quality_attempts: int = 0
    selected_for_registration: bool = False
    registration_recognized_streak: int = 0
    registration_recognized_name: str = ""


def recognized_user_payload(result: RecognitionResult) -> dict[str, str]:
    return {
        "name": result.user.name,
        "sr_code": result.user.sr_code,
        "gender": result.user.gender,
        "program": result.user.program,
        "confidence": f"{result.confidence:.2%}",
        "confidence_value": f"{result.confidence:.4f}",
        "threshold_value": f"{result.threshold:.4f}",
        "distance": f"{result.distance:.4f}",
    }
