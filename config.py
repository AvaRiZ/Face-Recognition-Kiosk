from dataclasses import dataclass


@dataclass
class AppConfig:
    model_path: str = "models/yolov10m-face.pt"
    db_path: str = "database/faces_improved.db"
    base_save_dir: str = "faces_improved"
    primary_model: str = "ArcFace"
    secondary_model: str = "Facenet"
    # Recognition similarity thresholds (higher = stricter match, fewer false accepts).
    primary_threshold: float = 0.7
    secondary_threshold: float = 0.6
    # Global minimum similarity for any match to be considered at all.
    base_threshold: float = 0.6
    # If True, thresholds can be adjusted at runtime based on quality signals.
    adaptive_threshold_enabled: bool = True

    # Face quality preset quick guide (copy these values into the two fields below):
    # - Lenient (dim/noisy camera, fewer misses, more risk): threshold=0.50, good=0.64
    # - Balanced (recommended default): threshold=0.56, good=0.70
    # - Strict (security-first, cleaner samples only): threshold=0.62, good=0.76
    # Rules of thumb:
    # - Raise threshold to reject poor frames more aggressively.
    # - Raise good threshold to make "high-quality" behavior harder to trigger.
    # - Keep good threshold >= threshold + 0.10 for stable behavior.

    # Minimum overall face-quality score required before recognition is attempted.
    # Good frames should naturally land at or above ~0.75 with this pipeline.
    face_quality_threshold: float = 0.50

    # Score considered "good quality" by adaptive logic.
    face_quality_good_threshold: float = 0.65
    # Reject detections smaller than this (in pixels).
    min_face_size: int = 50
    confidence_smoothing_window: int = 3
    detection_every_n_frames: int = 3
    recognition_cooldown_seconds: int = 4
    revalidation_interval_seconds: float = 6.0

    # Confidence needed before locking identity to avoid flicker.
    identity_lock_confidence_threshold: float = 0.7
    stability_time_required: float = 0.3
    position_tolerance: int = 200
    track_stale_seconds: float = 10.0
    
    # Candidate gating for enrollment / best-shot selection.
    candidate_min_confidence: float = 0.85
    candidate_consistent_frames: int = 2
    candidate_min_time_gap_seconds: float = 0.35
    candidate_static_motion_threshold_px: float = 2.0
    candidate_batch_min_size: int = 3
    candidate_max_spread: float = 0.20
    candidate_duplicate_similarity: float = 0.98
    max_embeddings_per_user_per_model: int = 30
    embedding_commit_interval_seconds: float = 3.0

    # Face-quality scoring presets (starter values for this section):
    # - Lenient scoring (accept more imperfect faces):
    #   area_low=48*48, sharpness_low=50, det_conf_low=0.35, dark_ratio_bad=0.35,
    #   bright_ratio_bad=0.58, dynamic_range_low=22
    # - Balanced scoring (current default behavior):
    #   area_low=56*56, sharpness_low=60, det_conf_low=0.40, dark_ratio_bad=0.30,
    #   bright_ratio_bad=0.50, dynamic_range_low=28
    # - Strict scoring (clean samples only):
    #   area_low=64*64, sharpness_low=75, det_conf_low=0.50, dark_ratio_bad=0.25,
    #   bright_ratio_bad=0.42, dynamic_range_low=35
    #
    # How scoring thresholds work:
    # - "*_low" and "*_bad" values describe poor quality boundaries.
    # - "*_high" and "*_good" values describe strong quality boundaries.
    # - Make low/bad stricter by increasing them for metrics where higher is better
    #   (size, sharpness, confidence, contrast, dynamic range).
    # - Make low/bad stricter by decreasing them for metrics where lower is better
    #   (dark/bright ratios, tilt/pose/alignment ratios).
    # Face-quality score components and thresholds.
    quality_face_area_low: int = 56 * 56
    quality_face_area_high: int = 170 * 170

    # Detector confidence thresholds used in quality scoring.
    # Raise these to trust only high-confidence detections.
    quality_detection_confidence_low: float = 0.40
    quality_detection_confidence_high: float = 0.82

    # Sharpness (blur) thresholds used in quality scoring.
    quality_sharpness_low: float = 8.0
    quality_sharpness_high: float = 60.0

    # Mean brightness thresholds used in quality scoring.
    # Higher low/high values will penalize dim or overexposed faces more heavily.
    quality_mean_intensity_low: float = 55.0
    quality_mean_intensity_high: float = 130.0

    # Hard-gate thresholds: fail fast before embedding extraction.
    quality_hard_gate_laplacian: float = 16.0
    quality_hard_gate_contrast: float = 10.0
    quality_hard_gate_mean_intensity: float = 50.0

    # Dark/bright pixel intensity cutoffs for exposure checks.
    # High dark threshold = more pixels considered underexposed, high bright threshold = more pixels considered overexposed.
    quality_dark_intensity_threshold: int = 33
    quality_bright_intensity_threshold: int = 215

    # Acceptable ratios of dark/bright pixels (lower is better).
    # Decrease *_bad values to be stricter about over/under-exposure.
    quality_dark_ratio_good: float = 0.13
    quality_dark_ratio_bad: float = 0.32
    quality_bright_ratio_good: float = 0.15
    quality_bright_ratio_bad: float = 0.50

    # Dynamic range (contrast) thresholds in grayscale.
    # Increase low/high to penalize flat, washed-out images more heavily.
    quality_dynamic_range_low: float = 40.0  # retained for diagnostics only
    quality_dynamic_range_high: float = 95.0

    # Canny edge thresholds used as a proxy for detail.
    quality_canny_low: int = 50
    quality_canny_high: int = 150

    # Landmark ratios for eye tilt / pose alignment (lower = more aligned).
    # Lower good/bad ratios to enforce more frontal and level faces.
    quality_eye_tilt_good_ratio: float = 0.11
    quality_eye_tilt_bad_ratio: float = 0.29
    quality_pose_good_ratio: float = 0.15
    quality_pose_bad_ratio: float = 0.80

    # Band/pose balance thresholds used in alignment scoring.
    # Lower values are stricter for alignment and face symmetry.
    quality_band_alignment_good_ratio: float = 0.09
    quality_band_alignment_bad_ratio: float = 0.24
    quality_pose_balance_good: float = 0.22
    quality_pose_balance_bad: float = 0.80

    # Contrast and aspect ratio bounds for quality scoring.
    # Raise contrast thresholds if low-contrast faces are passing too easily.
    # For aspect ratio, lower *_bad to reject extreme head angles/crops earlier.
    quality_contrast_low: float = 16.0
    quality_contrast_high: float = 38.0
    quality_aspect_ratio_good: float = 1.25
    quality_aspect_ratio_bad: float = 1.95

    # Weights for the final quality score (should sum ~1.0).
    # Increase a weight to make that factor influence the final score more.
    # Practical tuning examples:
    # - Motion blur issues: raise quality_weight_sharpness.
    # - Lighting issues: raise quality_weight_exposure and/or contrast.
    # - Side-angle issues: raise quality_weight_alignment and/or pose.
    quality_weight_size: float = 0.03
    quality_weight_sharpness: float = 0.10
    quality_weight_detection_confidence: float = 0.15
    quality_weight_alignment: float = 0.10
    quality_weight_pose: float = 0.10
    quality_weight_exposure: float = 0.29
    quality_weight_contrast: float = 0.20
    quality_weight_aspect_ratio: float = 0.03

    # Optional preprocessing to improve quality under difficult lighting.
    quality_use_clahe: bool = False
    quality_clahe_clip_limit: float = 2.0
    quality_clahe_tile_grid: int = 8
    quality_gamma: float = 1.00

    use_mediapipe_landmarks: bool = True
    mediapipe_max_faces: int = 5
    mediapipe_min_detection_confidence: float = 0.5
    mediapipe_min_tracking_confidence: float = 0.5
    mediapipe_face_landmarker_model_path: str = "models/face_landmarker.task"

    @property
    def models(self):
        return [self.primary_model, self.secondary_model]
