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
    course: str = ""
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


@dataclass
class RegistrationState:
    pending_registration: Optional[list[RegistrationSample]] = None
    in_progress: bool = False
    captured_samples: list[RegistrationSample] = field(default_factory=list)
    max_captures: int = 10
    manual_requested: bool = False
    manual_active: bool = False
    manual_track_id: Optional[int] = None

    @property
    def capture_count(self) -> int:
        return len(self.captured_samples)


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
        "course": result.user.course,
        "confidence": f"{result.confidence:.2%}",
        "distance": f"{result.distance:.4f}",
    }
