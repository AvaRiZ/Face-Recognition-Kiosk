from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import tensorflow as tf
import torch


@dataclass
class AppConfig:
    # ---------------------------
    # Paths and storage
    # ---------------------------
    model_path: str = "models/yolov8n-face.pt"
    db_path: str = "database/faces_improved.db"
    base_save_dir: str = "faces_improved"
    detector_dataset_dir: str = "detector_dataset"
    detector_train_split: str = "train"
    real_val_dataset_dir: str = "real_val_dataset"

    # ---------------------------
    # Validation frame capture
    # ---------------------------
    # Saves raw CCTV frames for offline detector validation.
    real_val_capture_enabled: bool = True
    # Save one frame every N frames while capture is enabled.
    real_val_capture_every_n_frames: int = 90
    # Hard cap to prevent unbounded disk growth.
    real_val_capture_max_frames: int = 300

    # ---------------------------
    # Embedding models
    # ---------------------------
    primary_model: str = "ArcFace"
    secondary_model: str = "Facenet"

    # ---------------------------
    # Recognition thresholds
    # ---------------------------
    # Minimum confidence required per model. Higher values reduce false accepts,
    # but may increase misses for hard faces (blur, side pose, low light).

    primary_threshold: float = 0.7
    secondary_threshold: float = 0.6

    # Global recognition threshold floor set from settings.
    # Increasing this generally makes recognition stricter system-wide.
    base_threshold: float = 0.5

    # ---------------------------
    # Face quality gates
    # ---------------------------
    # Faces below this score are skipped before recognition.
    face_quality_threshold: float = 0.58
    # "Good" quality target used for color/status and high-confidence actions.
    face_quality_good_threshold: float = 0.75

    # ---------------------------
    # Runtime and scheduling
    # ---------------------------
    min_face_size: int = 50
    confidence_smoothing_window: int = 3

    # Detector scheduling: run detection every N frames when enabled.
    # Increase N for better FPS, decrease N for fresher detections.
    detection_every_n_frames: int = 2
    enable_detection_frame_scheduling: bool = True

    # Prevent repeated recognition attempts on the same track too frequently.
    recognition_cooldown_seconds: int = 1

    # ---------------------------
    # Vectorized matching (Phase 1)
    # ---------------------------
    # 0 means evaluate all users after vectorized distance computation.
    # Set > 0 to evaluate only top-k nearest candidates per model.
    vector_search_top_k_per_model: int = 0

    # ---------------------------
    # Continuous learning controls
    # ---------------------------
    # Max number of stored embeddings per user per model.
    # Lower values reduce memory and matching cost; very low values can hurt recall.
    max_embeddings_per_user_per_model: int = 30

    # Minimum cosine distance from existing embeddings to accept a new sample.
    # Smaller value learns more aggressively; larger value learns only novel samples.
    embedding_novelty_min_distance: float = 0.08

    # Require this quality score before writing new learned embeddings.
    recognition_min_quality_for_learning: float = 0.75

    # ---------------------------
    # Tracking stability
    # ---------------------------
    # Required stable time before recognition is attempted.
    stability_time_required: float = 0.3
    # Pixel movement tolerance used by the stability tracker.
    position_tolerance: int = 200
    # Remove stale tracks after this many seconds.
    track_stale_seconds: float = 5.0

    # ---------------------------
    # Quality metric thresholds
    # ---------------------------
    # Most low/high pairs below follow the same rule:
    # - "low" is where score starts to become acceptable
    # - "high" is where score is considered strong
    quality_face_area_low: int = 50 * 50
    quality_face_area_high: int = 130 * 130
    quality_detection_confidence_low: float = 0.35
    quality_detection_confidence_high: float = 0.80
    quality_sharpness_low: float = 80.0
    quality_sharpness_high: float = 250.0
    quality_contrast_low: float = 20.0
    quality_contrast_high: float = 60.0
    quality_dark_intensity_threshold: int = 40
    quality_bright_intensity_threshold: int = 220
    quality_dark_ratio_good: float = 0.08
    quality_dark_ratio_bad: float = 0.35
    quality_bright_ratio_good: float = 0.05
    quality_bright_ratio_bad: float = 0.28
    quality_dynamic_range_low: float = 30.0
    quality_dynamic_range_high: float = 90.0
    quality_canny_low: int = 50
    quality_canny_high: int = 150
    quality_edge_density_low: float = 0.03
    quality_edge_density_high: float = 0.12
    quality_low_detail_std_threshold: float = 12.0
    quality_low_detail_ratio_good: float = 0.20
    quality_low_detail_ratio_bad: float = 0.65
    quality_eye_tilt_good_ratio: float = 0.08
    quality_eye_tilt_bad_ratio: float = 0.20
    quality_pose_good_ratio: float = 0.10
    quality_pose_bad_ratio: float = 0.30
    quality_band_alignment_good_ratio: float = 0.06
    quality_band_alignment_bad_ratio: float = 0.18
    quality_pose_balance_good: float = 0.15
    quality_pose_balance_bad: float = 0.45

    # ---------------------------
    # Quality score weights
    # ---------------------------
    # Combined quality score is a weighted blend of features.
    # Increase a weight to make that feature influence quality more.
    quality_weight_size: float = 0.22
    quality_weight_sharpness: float = 0.24
    quality_weight_detection_confidence: float = 0.20
    quality_weight_alignment: float = 0.14
    quality_weight_pose: float = 0.08
    quality_weight_exposure: float = 0.07
    quality_weight_contrast: float = 0.03
    quality_weight_occlusion: float = 0.02

    # ---------------------------
    # Device selection
    # ---------------------------
    torch_device_index: int = 0
    tf_use_gpu: bool = True

    @property
    def models(self) -> list[str]:
        return [self.primary_model, self.secondary_model]


def configure_devices(
    torch_device_index: int = 0,
    tf_use_gpu: bool = True,
    logger: Callable[[str, str], None] | None = None,
) -> None:
    """Configure Torch and TensorFlow device visibility."""
    if torch.cuda.is_available():
        try:
            if torch.cuda.device_count() > torch_device_index:
                torch.cuda.set_device(torch_device_index)
            else:
                torch.cuda.set_device(0)
        except Exception as exc:
            if logger:
                logger(f"Torch device selection warning: {exc}", "WARN")

    try:
        gpus = tf.config.list_physical_devices("GPU")
        if not tf_use_gpu:
            if gpus:
                tf.config.set_visible_devices([], "GPU")
        else:
            if gpus:
                if len(gpus) > torch_device_index:
                    tf.config.set_visible_devices(gpus[torch_device_index], "GPU")
                    tf.config.experimental.set_memory_growth(gpus[torch_device_index], True)
                else:
                    tf.config.set_visible_devices(gpus[0], "GPU")
                    tf.config.experimental.set_memory_growth(gpus[0], True)
    except Exception as exc:
        if logger:
            logger(f"TensorFlow GPU configuration warning: {exc}", "WARN")


def resolve_yolo_device(torch_device_index: int = 0) -> str:
    if not torch.cuda.is_available():
        return "cpu"
    if torch.cuda.device_count() > torch_device_index:
        return f"cuda:{torch_device_index}"
    return "cuda:0"
