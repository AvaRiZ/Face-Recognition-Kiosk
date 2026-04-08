from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import tensorflow as tf
import torch


@dataclass
class AppConfig:
    """Central application settings.

    Notes on the face quality thresholds:
    - `*_min` values are the hard minimums used to flag a failed check.
    - `*_good` values mark the point where that component reaches a full score.
    - `face_quality_threshold` is the pass line between `Poor` and `Acceptable`.
    - `face_quality_good_threshold` is the line for promoting a pass to `Good`.

    Practical tuning guide for `FaceQualityService`:
    - Increase a `*_min` threshold to reject more weak crops.
    - Decrease a `*_min` threshold to accept more difficult real-world captures.
    - Increase a `*_good` threshold when you want the `Good` label to mean a
      clearly cleaner sample.
    - Decrease a `*_good` threshold if too many usable faces stay stuck as
      `Acceptable`.
    - Tune one family at a time: size, detector confidence, sharpness,
      exposure, then landmark-based pose/truncation.
    - Re-test after each change because the final quality score is the average
      of multiple component scores, and any failed hard check forces the result
      below `face_quality_threshold`.
    """

    # ------------------------------------------------------------------
    # Paths and dataset locations
    # ------------------------------------------------------------------
    model_path: str = "models/yolov8n-face.pt"
    db_path: str = "database/faces_improved.db"
    base_save_dir: str = "faces_improved"
    detector_dataset_dir: str = "detector_dataset"
    detector_train_split: str = "train"
    real_val_dataset_dir: str = "real_val_dataset"

    # ------------------------------------------------------------------
    # Dataset capture / validation collection
    # ------------------------------------------------------------------
    real_val_capture_enabled: bool = True
    real_val_capture_every_n_frames: int = 90
    real_val_capture_max_frames: int = 300

    # ------------------------------------------------------------------
    # Recognition models and identity thresholds
    # ------------------------------------------------------------------
    primary_model: str = "ArcFace"
    secondary_model: str = "Facenet"
    primary_threshold: float = 0.7
    secondary_threshold: float = 0.6
    base_threshold: float = 0.5
    vector_index_top_k: int = 20

    # ------------------------------------------------------------------
    # Overall face quality decisions
    # ------------------------------------------------------------------
    # `face_quality_threshold`:
    # - Raise this to make registration / recognition gatekeeping stricter.
    # - Lower this if usable real-world faces are being rejected too often.
    face_quality_threshold: float = 0.58

    # `face_quality_good_threshold`:
    # - Raise this if you want the `Good` label to be harder to earn.
    # - Lower this if strong samples are rarely reaching `Good`.
    face_quality_good_threshold: float = 0.75

    # CLI/debug output for face quality assessment.
    # - Enable `quality_debug_enabled` to include richer quality diagnostics in
    #   CLI logs and on-screen labels.
    # - Keep `quality_debug_show_primary_issue=True` to surface the most likely
    #   reason a face was marked poor.
    # - Enable `quality_debug_show_all_scores` while tuning thresholds so you
    #   can inspect every component score and raw metric value.
    quality_debug_enabled: bool = True
    quality_debug_show_primary_issue: bool = True
    quality_debug_show_all_scores: bool = True

    # Minimum crop size used outside the quality scorer for fast filtering.
    min_face_size: int = 50

    # ------------------------------------------------------------------
    # Runtime behavior
    # ------------------------------------------------------------------
    # Registration capture counts:
    # - `registration_samples_per_pose_target` controls how many valid images
    #   are captured for each required pose during registration.
    # - `registration_retained_samples_per_pose` controls how many top-quality
    #   images per pose are kept for final enrollment.
    registration_samples_per_pose_target: int = 5
    registration_retained_samples_per_pose: int = 5
    confidence_smoothing_window: int = 3
    detection_every_n_frames: int = 2
    recognition_cooldown_seconds: int = 1
    stability_time_required: float = 0.3
    position_tolerance: int = 200
    track_stale_seconds: float = 5.0

    # ------------------------------------------------------------------
    # Quality scoring: face size
    # ------------------------------------------------------------------
    # Area is measured in pixels (`width * height` of the face crop).
    # Example:
    # - `50 * 50` means crops smaller than roughly 50px by 50px fail size.
    # - `130 * 130` means crops around that size get full size credit.
    # Tuning:
    # - Raise `quality_face_area_min` to reject small, low-detail faces.
    # - Lower it if your camera is farther away and valid faces look smaller.
    quality_face_area_min: int = 50 * 50
    quality_face_area_good: int = 130 * 130

    # ------------------------------------------------------------------
    # Quality scoring: detector confidence
    # ------------------------------------------------------------------
    # Tuning:
    # - Raise these if false detections are slipping through.
    # - Lower these if the detector is generally conservative but still right.
    quality_detection_confidence_min: float = 0.35
    quality_detection_confidence_good: float = 0.80

    # ------------------------------------------------------------------
    # Quality scoring: sharpness / blur
    # ------------------------------------------------------------------
    # Sharpness is based on Laplacian variance.
    # Tuning:
    # - Raise `quality_sharpness_min` to be stricter against blur.
    # - Lower it if motion blur is common but recognition still works.
    quality_sharpness_min: float = 60.0
    quality_sharpness_good: float = 128.0

    # ------------------------------------------------------------------
    # Quality scoring: brightness / exposure
    # ------------------------------------------------------------------
    # Brightness is the mean grayscale intensity on a 0-255 scale.
    # The "good" window is the comfort zone with full brightness credit.
    # Tuning:
    # - Raise `quality_brightness_min` if dark faces are hurting recognition.
    # - Lower it if your environment is dim and faces are still usable.
    # - Lower `quality_brightness_max` if overexposed faces should fail sooner.
    # - Widen the `good_min` to `good_max` band if lighting is more variable.
    quality_brightness_min: float = 55.0
    quality_brightness_good_min: float = 85.0
    quality_brightness_good_max: float = 185.0
    quality_brightness_max: float = 215.0

    # Dynamic range approximates how much tonal spread exists in the crop.
    # Low values often mean flat lighting or washed-out detail.
    # Tuning:
    # - Raise these to prefer richer contrast and facial detail.
    # - Lower them if your camera feed is naturally low-contrast.
    quality_dynamic_range_min: float = 35.0
    quality_dynamic_range_good: float = 85.0

    # ------------------------------------------------------------------
    # Quality scoring: landmark-based pose / truncation
    # ------------------------------------------------------------------
    # These apply only when landmarks are available.
    #
    # Eye tilt:
    # - Lower values mean a more level face.
    # - Lower the thresholds to become stricter about roll / alignment.
    quality_pose_eye_tilt_good: float = 0.15
    quality_pose_eye_tilt_max: float = 0.50

    # Yaw:
    # - Based on nose offset from the eye midpoint.
    # - Lower the thresholds to prefer more front-facing faces.
    quality_pose_yaw_good: float = 0.35
    quality_pose_yaw_max: float = 0.70

    # Landmark margin:
    # - Measures how close key landmarks are to the crop edges.
    # - Raise these to be stricter about cut-off / partially cropped faces.
    quality_landmark_margin_good: float = 0.10
    quality_landmark_margin_min: float = 0.04

    # Registration pose classifier thresholds.
    # These are used to classify the current head pose as front/left/right
    # using landmark yaw measured as nose offset from eye midpoint.
    registration_pose_front_max_yaw_ratio: float = 0.20
    registration_pose_side_min_yaw_ratio: float = 0.30

    # ------------------------------------------------------------------
    # Device configuration
    # ------------------------------------------------------------------
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
    """Configure Torch and TensorFlow device visibility.

    `torch_device_index` selects the preferred GPU. If that index does not
    exist, the code falls back to GPU 0. Set `tf_use_gpu=False` to force
    TensorFlow onto CPU even when CUDA is available.
    """
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
    """Return the device string expected by YOLO / PyTorch code."""
    if not torch.cuda.is_available():
        return "cpu"
    if torch.cuda.device_count() > torch_device_index:
        return f"cuda:{torch_device_index}"
    return "cuda:0"
