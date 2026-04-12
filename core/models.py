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
    pending_registration: Optional[list[RegistrationSample]] = None
    in_progress: bool = False
    captured_samples: list[RegistrationSample] = field(default_factory=list)
    required_poses: list[str] = field(default_factory=lambda: ["front", "left", "right"])
    current_pose_index: int = 0
    samples_by_pose: dict[str, list[RegistrationSample]] = field(default_factory=dict)
    pose_capture_counts: dict[str, int] = field(default_factory=dict)
    samples_per_pose_target: int = 10
    retained_samples_per_pose: int = 5
    max_captures: int = 30
    manual_requested: bool = False
    manual_active: bool = False
    manual_track_id: Optional[int] = None

    @property
    def capture_count(self) -> int:
        if self.pose_capture_counts:
            return int(sum(self.pose_capture_counts.values()))
        return len(self.captured_samples)

    @property
    def current_pose(self) -> Optional[str]:
        if not self.required_poses:
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


def recognized_user_payload(result: RecognitionResult) -> dict[str, str]:
    return {
        "name": result.user.name,
        "sr_code": result.user.sr_code,
        "gender": result.user.gender,
        "program": result.user.program,
        "confidence": f"{result.confidence:.2%}",
        "distance": f"{result.distance:.4f}",
    }
