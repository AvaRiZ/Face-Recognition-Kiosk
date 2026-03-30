from dataclasses import dataclass


@dataclass
class AppConfig:
    model_path: str = "models/yolov8n-face.pt"
    db_path: str = "database/faces_improved.db"
    base_save_dir: str = "faces_improved"
    primary_model: str = "ArcFace"
    secondary_model: str = "Facenet"
    primary_threshold: float = 0.7
    secondary_threshold: float = 0.6
    base_threshold: float = 0.5
    adaptive_threshold_enabled: bool = True
    face_quality_threshold: float = 0.56
    face_quality_good_threshold: float = 0.70
    min_face_size: int = 50
    confidence_smoothing_window: int = 3
    detection_every_n_frames: int = 3
    recognition_cooldown_seconds: int = 4
    revalidation_interval_seconds: float = 6.0
    identity_lock_confidence_threshold: float = 0.7
    stability_time_required: float = 0.3
    position_tolerance: int = 200
    track_stale_seconds: float = 10.0
    candidate_min_confidence: float = 0.85
    candidate_consistent_frames: int = 2
    candidate_min_time_gap_seconds: float = 0.35
    candidate_static_motion_threshold_px: float = 4.0
    candidate_batch_min_size: int = 3
    candidate_max_spread: float = 0.20
    candidate_duplicate_similarity: float = 0.98
    max_embeddings_per_user_per_model: int = 30
    embedding_commit_interval_seconds: float = 3.0

    quality_face_area_low: int = 56 * 56
    quality_face_area_high: int = 170 * 170

    quality_detection_confidence_low: float = 0.40
    quality_detection_confidence_high: float = 0.82

    quality_sharpness_low: float = 60.0
    quality_sharpness_high: float = 100.0

    quality_dark_intensity_threshold: int = 45
    quality_bright_intensity_threshold: int = 215

    quality_dark_ratio_good: float = 0.10
    quality_dark_ratio_bad: float = 0.60
    quality_bright_ratio_good: float = 0.08
    quality_bright_ratio_bad: float = 0.35

    quality_dynamic_range_low: float = 28.0
    quality_dynamic_range_high: float = 95.0

    quality_canny_low: int = 50
    quality_canny_high: int = 150

    quality_eye_tilt_good_ratio: float = 0.11
    quality_eye_tilt_bad_ratio: float = 0.29
    quality_pose_good_ratio: float = 0.15
    quality_pose_bad_ratio: float = 0.80

    quality_band_alignment_good_ratio: float = 0.09
    quality_band_alignment_bad_ratio: float = 0.24
    quality_pose_balance_good: float = 0.22
    quality_pose_balance_bad: float = 0.80

    quality_contrast_low: float = 20.0
    quality_contrast_high: float = 58.0
    quality_aspect_ratio_good: float = 1.25
    quality_aspect_ratio_bad: float = 1.95

    quality_weight_size: float = 0.10
    quality_weight_sharpness: float = 0.28
    quality_weight_detection_confidence: float = 0.14
    quality_weight_alignment: float = 0.14
    quality_weight_pose: float = 0.10
    quality_weight_exposure: float = 0.15
    quality_weight_contrast: float = 0.15
    quality_weight_aspect_ratio: float = 0.05

    quality_use_clahe: bool = True
    quality_clahe_clip_limit: float = 2.0
    quality_clahe_tile_grid: int = 8
    quality_gamma: float = 1.08

    use_mediapipe_landmarks: bool = True
    mediapipe_max_faces: int = 5
    mediapipe_min_detection_confidence: float = 0.5
    mediapipe_min_tracking_confidence: float = 0.5
    mediapipe_face_landmarker_model_path: str = "models/face_landmarker.task"

    @property
    def models(self):
        return [self.primary_model, self.secondary_model]
