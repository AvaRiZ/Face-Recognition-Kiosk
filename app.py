import sys
import subprocess
import cv2
import numpy as np
import sqlite3
import os
import time
import pickle
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import statistics
import torch
import tensorflow as tf
from ultralytics import YOLO

def log_header(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

def log_step(message, status="OK"):
    print(f"[{status}] {message}")

def configure_devices(torch_device_index=1, tf_use_gpu=False):
    """Configure Torch to use CUDA and force TensorFlow/DeepFace to CPU if desired."""
    # Torch / Ultralytics (keep CUDA enabled if available)
    if torch.cuda.is_available():
        if torch.cuda.device_count() > torch_device_index:
            torch.cuda.set_device(torch_device_index)
        else:
            torch.cuda.set_device(0)

    # TensorFlow / DeepFace (force CPU when tf_use_gpu is False)
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
    except Exception as e:
        print(f"GPU configuration warning (TensorFlow): {e}")

configure_devices(torch_device_index=0, tf_use_gpu=True)

# Import DeepFace only after TensorFlow GPU configuration
from deepface import DeepFace

def log_gpu_info():
    """Log GPU name for Torch and TensorFlow."""
    # Torch
    if torch.cuda.is_available():
        try:
            current_device = torch.cuda.current_device()
            torch_name = torch.cuda.get_device_name(current_device)
            log_step(f"Torch CUDA device: {torch_name} (device {current_device})")
        except Exception as e:
            log_step(f"Torch CUDA info unavailable: {e}", status="WARN")
    else:
        log_step("Torch CUDA not available", status="WARN")

    # TensorFlow (DeepFace)
    try:
        tf_gpus = tf.config.list_logical_devices("GPU")
        if tf_gpus:
            log_step(f"TensorFlow GPU visible: {tf_gpus[0].name}")
        else:
            log_step("TensorFlow GPU hidden (CPU mode)", status="OK")
    except Exception as e:
        log_step(f"TensorFlow GPU info unavailable: {e}", status="WARN")
        log_step("TensorFlow GPU check failed. DeepFace may run on CPU.", status="WARN")

log_gpu_info()

# -------------------------------
# Configuration - Two-Factor Verification
# -------------------------------
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
    face_quality_threshold: float = 0.58
    face_quality_good_threshold: float = 0.75
    min_face_size: int = 50
    confidence_smoothing_window: int = 3
    detection_every_n_frames: int = 2
    recognition_cooldown_seconds: int = 2
    stability_time_required: float = 0.3
    position_tolerance: int = 200
    track_stale_seconds: float = 5.0

    # Face quality scoring thresholds
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

    # Face quality scoring weights (recognition-focused priorities)
    quality_weight_size: float = 0.22
    quality_weight_sharpness: float = 0.24
    quality_weight_detection_confidence: float = 0.20
    quality_weight_alignment: float = 0.14
    quality_weight_pose: float = 0.08
    quality_weight_exposure: float = 0.07
    quality_weight_contrast: float = 0.03
    quality_weight_occlusion: float = 0.02

    @property
    def models(self):
        return [self.primary_model, self.secondary_model]


@dataclass
class AppState:
    all_user_embeddings: list = field(default_factory=list)
    user_info: list = field(default_factory=list)
    user_count: int = 0
    pending_registration: Optional[list] = None
    recognized_user: Optional[dict] = None
    registration_in_progress: bool = False
    captured_faces_for_registration: list = field(default_factory=list)
    face_capture_count: int = 0
    max_captures_for_registration: int = 3
    manual_registration_requested: bool = False
    manual_registration_active: bool = False
    manual_registration_track_id: Optional[int] = None
    face_stability_tracker: dict = field(default_factory=dict)
    tracked_identities: dict = field(default_factory=dict)


CONFIG = AppConfig()
STATE = AppState()

# -------------------------------
# YOLOv8 model
# -------------------------------
log_step("Loading YOLOv8 face detection model...")
yolo_device = "cpu"
if torch.cuda.is_available():
    if torch.cuda.device_count() > 1:
        yolo_device = "cuda:1"
    else:
        yolo_device = "cuda:0"

model = YOLO(CONFIG.model_path)
try:
    model.to(yolo_device)
except Exception as e:
    log_step(f"YOLO GPU warning: {e}", status="WARN")
log_step(f"YOLOv8 model loaded on {yolo_device}")

# -------------------------------
# DeepFace configuration - Two-Factor Verification
# -------------------------------
log_step(f"Two-Factor Verification: {CONFIG.primary_model} + {CONFIG.secondary_model}")

# -------------------------------
# Database setup (SQLite) with improved schema
# -------------------------------
def init_db():
    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            sr_code TEXT UNIQUE,
            course TEXT,
            embeddings BLOB NOT NULL,
            image_paths TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS recognition_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            confidence REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # Backward-compatible schema upgrades for richer recognition diagnostics.
    c.execute("PRAGMA table_info(recognition_log)")
    existing_columns = {row[1] for row in c.fetchall()}
    extra_columns = {
        "primary_confidence": "REAL",
        "secondary_confidence": "REAL",
        "primary_distance": "REAL",
        "secondary_distance": "REAL",
        "face_quality": "REAL",
        "method": "TEXT DEFAULT 'two-factor'",
    }
    for col_name, col_type in extra_columns.items():
        if col_name not in existing_columns:
            c.execute(f"ALTER TABLE recognition_log ADD COLUMN {col_name} {col_type}")
    
    conn.commit()
    conn.close()

def save_user_with_multiple_embeddings(embeddings_by_model, image_paths, name, sr_code, course):
    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    
    c.execute("SELECT user_id FROM users WHERE sr_code = ?", (sr_code,))
    existing = c.fetchone()
    
    if existing:
        print(f"User with SR Code {sr_code} already exists. Updating...")
        user_id = existing[0]
        
        c.execute("SELECT embeddings FROM users WHERE user_id = ?", (user_id,))
        existing_emb_blob = c.fetchone()[0]
        if existing_emb_blob:
            existing_embeddings = pickle.loads(existing_emb_blob)
            all_embeddings = merge_embeddings_by_model(existing_embeddings, embeddings_by_model)
        else:
            all_embeddings = normalize_embeddings_by_model(embeddings_by_model)
        
        embeddings_blob = pickle.dumps(all_embeddings)
        c.execute("""
            UPDATE users 
            SET name = ?, course = ?, embeddings = ?, image_paths = ?, 
                embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (name, course, embeddings_blob, ';'.join(image_paths), infer_embedding_dim(all_embeddings), user_id))
    else:
        embeddings_by_model = normalize_embeddings_by_model(embeddings_by_model)
        embeddings_blob = pickle.dumps(embeddings_by_model)
        embedding_dim = infer_embedding_dim(embeddings_by_model)
        c.execute("""
            INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, sr_code, course, embeddings_blob, ';'.join(image_paths), embedding_dim))
        user_id = c.lastrowid
    
    conn.commit()
    conn.close()
    total_emb = count_embeddings(embeddings_by_model)
    print(f"✓ User saved/updated with ID: {user_id} ({total_emb} embeddings across models)")
    return user_id

def _normalize_embedding_list(value):
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        if value.ndim == 1:
            return [value.astype(np.float32, copy=False)]
        if value.ndim == 2:
            return [row.astype(np.float32, copy=False) for row in value]
        return []
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return []
        first = value[0]
        if isinstance(first, np.ndarray):
            return [v.astype(np.float32, copy=False) for v in value if isinstance(v, np.ndarray)]
        if isinstance(first, (list, tuple, np.ndarray)):
            out = []
            for v in value:
                arr = v if isinstance(v, np.ndarray) else np.array(v, dtype=np.float32)
                if arr.ndim == 1:
                    out.append(arr.astype(np.float32, copy=False))
                elif arr.ndim == 2:
                    out.extend([row.astype(np.float32, copy=False) for row in arr])
            return out
        if isinstance(first, (int, float, np.floating, np.integer)):
            arr = np.array(value, dtype=np.float32)
            if arr.ndim == 1:
                return [arr]
            return []
    if isinstance(value, (int, float, np.floating, np.integer)):
        return [np.array([value], dtype=np.float32)]
    return []

def normalize_embeddings_by_model(embeddings_by_model):
    if embeddings_by_model is None:
        return {}
    if not isinstance(embeddings_by_model, dict):
        return {}
    normalized = {}
    for model_name, value in embeddings_by_model.items():
        normalized[model_name] = _normalize_embedding_list(value)
    return normalized

def merge_embeddings_by_model(existing, new):
    """Merge embeddings dicts: {model_name: [embeddings...]}"""
    merged = {}
    for model_name, emb_list in normalize_embeddings_by_model(existing).items():
        merged[model_name] = list(emb_list)
    for model_name, emb_list in normalize_embeddings_by_model(new).items():
        if model_name not in merged:
            merged[model_name] = []
        merged[model_name].extend(list(emb_list))
    return merged

def infer_embedding_dim(embeddings_by_model):
    """Infer embedding dim from the first available embedding"""
    if not embeddings_by_model:
        return 0
    normalized = normalize_embeddings_by_model(embeddings_by_model)
    for emb_list in normalized.values():
        if emb_list:
            emb = emb_list[0]
            if isinstance(emb, np.ndarray) and emb.ndim == 1:
                return emb.shape[0]
            if isinstance(emb, (list, tuple)):
                return len(emb)
    return 0

def count_embeddings(embeddings_by_model):
    if not embeddings_by_model:
        return 0
    normalized = normalize_embeddings_by_model(embeddings_by_model)
    return sum(len(v) for v in normalized.values() if v)

def _coerce_float(value):
    """Best-effort conversion for SQLite values that may be REAL, TEXT, or BLOB."""
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes):
        if not value:
            return None
        try:
            return float(value.decode("utf-8").strip())
        except Exception:
            pass
        if len(value) == 8:
            try:
                return float(np.frombuffer(value, dtype=np.float64, count=1)[0])
            except Exception:
                pass
        if len(value) == 4:
            try:
                return float(np.frombuffer(value, dtype=np.float32, count=1)[0])
            except Exception:
                pass
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None

def load_all_embeddings():
    """Load all embeddings for all users"""
    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute("SELECT user_id, name, sr_code, embeddings FROM users")
    rows = c.fetchall()
    conn.close()
    
    all_embeddings = []
    user_info = []
    
    for user_id, name, sr_code, emb_blob in rows:
        if emb_blob:
            embeddings_by_model = normalize_embeddings_by_model(pickle.loads(emb_blob))
            all_embeddings.append(embeddings_by_model)
            user_info.append({
                'id': user_id,
                'name': name,
                'sr_code': sr_code
            })
    
    log_step(f"Loaded {len(all_embeddings)} users with embeddings")
    return all_embeddings, user_info

def log_recognition(
    user_id,
    confidence,
    primary_confidence=None,
    secondary_confidence=None,
    primary_distance=None,
    secondary_distance=None,
    face_quality=None,
    method="two-factor",
):
    """Log recognition events for analysis"""
    confidence = _coerce_float(confidence)
    if confidence is None:
        confidence = 0.0
    primary_confidence = _coerce_float(primary_confidence)
    secondary_confidence = _coerce_float(secondary_confidence)
    primary_distance = _coerce_float(primary_distance)
    secondary_distance = _coerce_float(secondary_distance)
    face_quality = _coerce_float(face_quality)

    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO recognition_log (
            user_id, confidence, primary_confidence, secondary_confidence,
            primary_distance, secondary_distance, face_quality, method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            confidence,
            primary_confidence,
            secondary_confidence,
            primary_distance,
            secondary_distance,
            face_quality,
            method,
        ),
    )
    conn.commit()
    conn.close()

# -------------------------------
# Setup directories
# -------------------------------
os.makedirs(CONFIG.base_save_dir, exist_ok=True)

# -------------------------------
# Initialize database
# -------------------------------
init_db()

# Load existing embeddings
STATE.all_user_embeddings, STATE.user_info = load_all_embeddings()
STATE.user_count = len(STATE.user_info)

# -------------------------------
# Improved thresholds and parameters
# -------------------------------
# Values now centralized under CONFIG

# -------------------------------
# Face quality assessment
# -------------------------------
def _clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _three_level_score(value, low_threshold, high_threshold):
    """Map a metric to {0.0, 0.5, 1.0} using configurable thresholds."""
    value = float(value)
    if value < low_threshold:
        return 0.0
    if value < high_threshold:
        return 0.5
    return 1.0


def _score_higher_better(value, low_threshold, high_threshold):
    if high_threshold <= low_threshold:
        return 1.0 if value >= high_threshold else 0.0
    return _clamp01((float(value) - low_threshold) / (high_threshold - low_threshold))


def _score_lower_better(value, good_threshold, bad_threshold):
    if bad_threshold <= good_threshold:
        return 1.0 if value <= good_threshold else 0.0
    return _clamp01(1.0 - ((float(value) - good_threshold) / (bad_threshold - good_threshold)))


def _to_grayscale(face_crop):
    if len(face_crop.shape) == 3:
        return cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    return face_crop


def _normalize_landmarks(landmarks):
    """Normalize landmarks into a dict with optional keys:
    left_eye, right_eye, nose, mouth_left, mouth_right, mouth."""
    if landmarks is None:
        return None

    normalized = {}

    def _to_point(value):
        if value is None:
            return None
        arr = np.asarray(value, dtype=np.float32).reshape(-1)
        if arr.shape[0] < 2:
            return None
        return float(arr[0]), float(arr[1])

    if isinstance(landmarks, dict):
        for key in ("left_eye", "right_eye", "nose", "mouth_left", "mouth_right", "mouth"):
            pt = _to_point(landmarks.get(key))
            if pt is not None:
                normalized[key] = pt
    else:
        pts = np.asarray(landmarks, dtype=np.float32)
        if pts.ndim == 2 and pts.shape[1] >= 2:
            mapped = {}
            if pts.shape[0] >= 1:
                mapped["left_eye"] = (float(pts[0, 0]), float(pts[0, 1]))
            if pts.shape[0] >= 2:
                mapped["right_eye"] = (float(pts[1, 0]), float(pts[1, 1]))
            if pts.shape[0] >= 3:
                mapped["nose"] = (float(pts[2, 0]), float(pts[2, 1]))
            if pts.shape[0] >= 4:
                mapped["mouth_left"] = (float(pts[3, 0]), float(pts[3, 1]))
            if pts.shape[0] >= 5:
                mapped["mouth_right"] = (float(pts[4, 0]), float(pts[4, 1]))
            normalized.update(mapped)

    if "mouth" not in normalized:
        ml = normalized.get("mouth_left")
        mr = normalized.get("mouth_right")
        if ml is not None and mr is not None:
            normalized["mouth"] = ((ml[0] + mr[0]) * 0.5, (ml[1] + mr[1]) * 0.5)

    if not normalized:
        return None
    return normalized


def _alignment_pose_from_landmarks(landmarks):
    left_eye = landmarks.get("left_eye")
    right_eye = landmarks.get("right_eye")
    nose = landmarks.get("nose")
    mouth = landmarks.get("mouth")

    if left_eye is None or right_eye is None:
        return 0.5, 0.5

    eye_dx = right_eye[0] - left_eye[0]
    eye_dy = right_eye[1] - left_eye[1]
    eye_dist = max(np.hypot(eye_dx, eye_dy), 1.0)

    eye_tilt_ratio = abs(eye_dy) / eye_dist
    eye_alignment_score = _score_lower_better(
        eye_tilt_ratio,
        CONFIG.quality_eye_tilt_good_ratio,
        CONFIG.quality_eye_tilt_bad_ratio,
    )

    vertical_order_score = 0.5
    if nose is not None and mouth is not None:
        eyes_top = max(left_eye[1], right_eye[1])
        vertical_order_score = 1.0 if (eyes_top < nose[1] < mouth[1]) else 0.2
    elif nose is not None:
        eyes_top = max(left_eye[1], right_eye[1])
        vertical_order_score = 1.0 if eyes_top < nose[1] else 0.3

    alignment_score = _clamp01(0.75 * eye_alignment_score + 0.25 * vertical_order_score)

    pose_score = 0.5
    eye_mid_x = (left_eye[0] + right_eye[0]) * 0.5
    half_eye_span = max(abs(right_eye[0] - left_eye[0]) * 0.5, 1.0)
    if nose is not None:
        yaw_ratio = abs(nose[0] - eye_mid_x) / half_eye_span
        pose_score = _score_lower_better(
            yaw_ratio,
            CONFIG.quality_pose_good_ratio,
            CONFIG.quality_pose_bad_ratio,
        )
    elif mouth is not None:
        yaw_ratio = abs(mouth[0] - eye_mid_x) / half_eye_span
        pose_score = _score_lower_better(
            yaw_ratio,
            CONFIG.quality_pose_good_ratio,
            CONFIG.quality_pose_bad_ratio,
        )

    return alignment_score, _clamp01(pose_score)


def _band_center_x(binary_band):
    ys, xs = np.where(binary_band > 0)
    if xs.size == 0:
        return None
    return float(np.mean(xs))


def _approximate_alignment_pose(edges):
    """Landmark-free alignment/pose proxies based on horizontal feature consistency."""
    h, w = edges.shape[:2]
    if h < 20 or w < 20:
        return 0.5, 0.5

    bands = ((0.18, 0.42), (0.40, 0.64), (0.62, 0.88))
    centers = []
    for y_start_ratio, y_end_ratio in bands:
        y1 = max(0, int(h * y_start_ratio))
        y2 = min(h, int(h * y_end_ratio))
        if y2 <= y1:
            continue
        center_x = _band_center_x(edges[y1:y2, :])
        if center_x is not None:
            centers.append(center_x)

    if len(centers) >= 2:
        center_spread_ratio = float(np.std(centers)) / max(float(w), 1.0)
        alignment_score = _score_lower_better(
            center_spread_ratio,
            CONFIG.quality_band_alignment_good_ratio,
            CONFIG.quality_band_alignment_bad_ratio,
        )
    else:
        alignment_score = 0.5

    y1 = int(h * 0.25)
    y2 = int(h * 0.80)
    center_band = edges[y1:y2, :]
    if center_band.size == 0 or w < 2:
        return alignment_score, 0.5

    left_density = float(np.mean(center_band[:, :w // 2] > 0))
    right_density = float(np.mean(center_band[:, w // 2:] > 0))
    lr_sum = left_density + right_density
    lr_imbalance = abs(left_density - right_density) / max(lr_sum, 1e-6)
    pose_score = _score_lower_better(
        lr_imbalance,
        CONFIG.quality_pose_balance_good,
        CONFIG.quality_pose_balance_bad,
    )
    return _clamp01(alignment_score), _clamp01(pose_score)


def _compute_exposure_metrics(gray):
    gray_f = gray.astype(np.float32, copy=False)
    brightness = float(np.mean(gray_f))
    contrast = float(np.std(gray_f))

    dark_ratio = float(np.mean(gray_f <= CONFIG.quality_dark_intensity_threshold))
    bright_ratio = float(np.mean(gray_f >= CONFIG.quality_bright_intensity_threshold))

    p5, p95 = np.percentile(gray_f, [5, 95])
    dynamic_range = float(p95 - p5)

    underexposed_score = _score_lower_better(
        dark_ratio,
        CONFIG.quality_dark_ratio_good,
        CONFIG.quality_dark_ratio_bad,
    )
    overexposed_score = _score_lower_better(
        bright_ratio,
        CONFIG.quality_bright_ratio_good,
        CONFIG.quality_bright_ratio_bad,
    )
    range_score = _score_higher_better(
        dynamic_range,
        CONFIG.quality_dynamic_range_low,
        CONFIG.quality_dynamic_range_high,
    )

    exposure_score = _clamp01(0.45 * underexposed_score + 0.45 * overexposed_score + 0.10 * range_score)
    contrast_score = _three_level_score(
        contrast,
        CONFIG.quality_contrast_low,
        CONFIG.quality_contrast_high,
    )

    return {
        "brightness": brightness,
        "contrast": contrast,
        "dark_ratio": dark_ratio,
        "bright_ratio": bright_ratio,
        "dynamic_range": dynamic_range,
        "exposure_score": exposure_score,
        "contrast_score": contrast_score,
    }


def _compute_detail_metrics(gray, edges):
    edge_density = float(np.mean(edges > 0))
    edge_density_score = _score_higher_better(
        edge_density,
        CONFIG.quality_edge_density_low,
        CONFIG.quality_edge_density_high,
    )

    # Fast 4x4 local-detail map over a normalized 32x32 crop.
    gray_small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    blocks = gray_small.reshape(4, 8, 4, 8)
    local_std = blocks.std(axis=(1, 3))
    low_detail_ratio = float(np.mean(local_std < CONFIG.quality_low_detail_std_threshold))

    low_detail_score = _score_lower_better(
        low_detail_ratio,
        CONFIG.quality_low_detail_ratio_good,
        CONFIG.quality_low_detail_ratio_bad,
    )
    occlusion_score = _clamp01(0.6 * edge_density_score + 0.4 * low_detail_score)

    return {
        "edge_density": edge_density,
        "low_detail_ratio": low_detail_ratio,
        "occlusion_score": occlusion_score,
    }


def assess_face_quality(face_crop, detection_confidence=None, landmarks=None):
    """Recognition-oriented face quality scoring.

    Returns:
        (quality_score, quality_status, debug_info)
    """
    if face_crop is None or face_crop.size == 0:
        debug_info = {
            "sharpness": 0.0,
            "brightness": 0.0,
            "contrast": 0.0,
            "alignment_score": 0.0,
            "detection_confidence": float(detection_confidence) if detection_confidence is not None else 0.0,
        }
        return 0.0, "Poor", debug_info

    h, w = face_crop.shape[:2]
    area = h * w
    gray = _to_grayscale(face_crop)

    size_score = _three_level_score(area, CONFIG.quality_face_area_low, CONFIG.quality_face_area_high)

    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_score = _three_level_score(
        laplacian_var,
        CONFIG.quality_sharpness_low,
        CONFIG.quality_sharpness_high,
    )

    confidence_value = float(detection_confidence) if detection_confidence is not None else 0.5
    confidence_value = _clamp01(confidence_value)
    detection_score = _three_level_score(
        confidence_value,
        CONFIG.quality_detection_confidence_low,
        CONFIG.quality_detection_confidence_high,
    )

    exposure_metrics = _compute_exposure_metrics(gray)

    edges = cv2.Canny(gray, CONFIG.quality_canny_low, CONFIG.quality_canny_high)
    detail_metrics = _compute_detail_metrics(gray, edges)

    normalized_landmarks = _normalize_landmarks(landmarks)
    if normalized_landmarks is not None:
        alignment_score, pose_score = _alignment_pose_from_landmarks(normalized_landmarks)
        alignment_source = "landmarks"
    else:
        alignment_score, pose_score = _approximate_alignment_pose(edges)
        alignment_source = "approx"

    weighted_sum = (
        CONFIG.quality_weight_size * size_score
        + CONFIG.quality_weight_sharpness * sharpness_score
        + CONFIG.quality_weight_detection_confidence * detection_score
        + CONFIG.quality_weight_alignment * alignment_score
        + CONFIG.quality_weight_pose * pose_score
        + CONFIG.quality_weight_exposure * exposure_metrics["exposure_score"]
        + CONFIG.quality_weight_contrast * exposure_metrics["contrast_score"]
        + CONFIG.quality_weight_occlusion * detail_metrics["occlusion_score"]
    )

    weights_total = (
        CONFIG.quality_weight_size
        + CONFIG.quality_weight_sharpness
        + CONFIG.quality_weight_detection_confidence
        + CONFIG.quality_weight_alignment
        + CONFIG.quality_weight_pose
        + CONFIG.quality_weight_exposure
        + CONFIG.quality_weight_contrast
        + CONFIG.quality_weight_occlusion
    )
    quality_score = _clamp01(weighted_sum / max(weights_total, 1e-6))

    if quality_score >= CONFIG.face_quality_good_threshold:
        quality_status = "Good"
    elif quality_score >= CONFIG.face_quality_threshold:
        quality_status = "Acceptable"
    else:
        quality_status = "Poor"

    debug_info = {
        "sharpness": laplacian_var,
        "brightness": exposure_metrics["brightness"],
        "contrast": exposure_metrics["contrast"],
        "alignment_score": alignment_score,
        "detection_confidence": confidence_value,
        "pose_score": pose_score,
        "size_score": size_score,
        "sharpness_score": sharpness_score,
        "exposure_score": exposure_metrics["exposure_score"],
        "dark_ratio": exposure_metrics["dark_ratio"],
        "bright_ratio": exposure_metrics["bright_ratio"],
        "edge_density": detail_metrics["edge_density"],
        "low_detail_ratio": detail_metrics["low_detail_ratio"],
        "occlusion_score": detail_metrics["occlusion_score"],
        "alignment_source": alignment_source,
    }

    return quality_score, quality_status, debug_info

# -------------------------------
# Improved embedding extraction - Two-Factor Verification
# -------------------------------
def extract_embedding_ensemble(face_crop):
    """Extract embeddings using BOTH DeepFace models for Two-Factor Verification"""
    try:
        if len(face_crop.shape) == 3 and face_crop.shape[2] == 3:
            face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        else:
            if len(face_crop.shape) == 2:
                face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_GRAY2RGB)
            else:
                face_rgb = face_crop
        
        embeddings = {}
        
        # Extract embeddings from BOTH models for Two-Factor Verification
        for model_name in CONFIG.models:
            try:
                embedding_obj = DeepFace.represent(
                    img_path=face_rgb,
                    model_name=model_name,
                    enforce_detection=False,
                    detector_backend='skip',
                    align=True,
                    normalization='base'
                )
                
                embedding = np.array(embedding_obj[0]['embedding'], dtype=np.float32)
                
                # L2 normalization
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    embedding = embedding / norm
                
                embeddings[model_name] = [embedding]
                print(f"  ✓ {model_name} embedding extracted")
                
            except Exception as e:
                print(f"  ✗ {model_name} failed: {e}")
        
        return embeddings
        
    except Exception as e:
        print(f"Embedding extraction error: {e}")
        return []

# -------------------------------
# Advanced face matching
# -------------------------------
class FaceRecognitionSystem:
    def __init__(self):
        self.recognition_history = {}
        self.confidence_smoothing = {}
        self.adaptive_thresholds = {}
    
    def calculate_dynamic_threshold(self, user_id, face_quality):
        """Calculate adaptive threshold based on face quality and history"""
        base_threshold = CONFIG.base_threshold
        
        if face_quality < 0.5:
            quality_adjustment = 0.1
        elif face_quality < 0.7:
            quality_adjustment = 0.05
        else:
            quality_adjustment = -0.05
        
        if user_id in self.recognition_history:
            history = self.recognition_history[user_id]
            avg_confidence = statistics.mean(history) if len(history) > 0 else 0.5
            history_adjustment = (0.5 - avg_confidence) * 0.2
        else:
            history_adjustment = 0
        
        dynamic_threshold = base_threshold + quality_adjustment + history_adjustment
        return max(0.2, min(0.6, dynamic_threshold))
    
    def smooth_confidence(self, user_id, confidence):
        """Apply smoothing to confidence scores"""
        if user_id not in self.confidence_smoothing:
            self.confidence_smoothing[user_id] = deque(maxlen=CONFIG.confidence_smoothing_window)
        
        self.confidence_smoothing[user_id].append(confidence)
        smoothed = statistics.mean(self.confidence_smoothing[user_id])
        return smoothed
    
    def find_best_match(self, query_embeddings, user_embeddings_list, user_info, face_quality):
        """Find the best match using Two-Factor Verification - BOTH models must confirm"""
        
        # Check if we have embeddings from both models
        if CONFIG.primary_model not in query_embeddings or CONFIG.secondary_model not in query_embeddings:
            print(f"  Warning: Need embeddings from both models for 2-factor verification")
            print(f"  Current models: {list(query_embeddings.keys())}")
            return None, []
        
        primary_emb = query_embeddings.get(CONFIG.primary_model)
        secondary_emb = query_embeddings.get(CONFIG.secondary_model)
        if isinstance(primary_emb, list) and primary_emb:
            primary_emb = primary_emb[0]
        if isinstance(secondary_emb, list) and secondary_emb:
            secondary_emb = secondary_emb[0]

        best_match = None
        best_user_idx = -1
        best_primary_dist = float('inf')
        best_secondary_dist = float('inf')

        # Compare against all users
        for user_idx, user_embeddings_by_model in enumerate(user_embeddings_list):
            user_id = user_info[user_idx]['id']
            
            if user_embeddings_by_model is None or not isinstance(user_embeddings_by_model, dict):
                continue
            if CONFIG.primary_model not in user_embeddings_by_model or CONFIG.secondary_model not in user_embeddings_by_model:
                continue
            
            # Find best match for PRIMARY model
            primary_best_dist = float('inf')
            for user_embedding in user_embeddings_by_model.get(CONFIG.primary_model, []):
                if user_embedding is None or not isinstance(user_embedding, np.ndarray):
                    continue
                if user_embedding.size == 0 or user_embedding.ndim != 1:
                    continue
                if primary_emb.shape != user_embedding.shape:
                    continue
                
                try:
                    distance = 1 - np.dot(primary_emb, user_embedding)
                    primary_best_dist = min(primary_best_dist, distance)
                except:
                    continue
            
            # Find best match for SECONDARY model
            secondary_best_dist = float('inf')
            for user_embedding in user_embeddings_by_model.get(CONFIG.secondary_model, []):
                if user_embedding is None or not isinstance(user_embedding, np.ndarray):
                    continue
                if user_embedding.size == 0 or user_embedding.ndim != 1:
                    continue
                if secondary_emb.shape != user_embedding.shape:
                    continue
                
                try:
                    distance = 1 - np.dot(secondary_emb, user_embedding)
                    secondary_best_dist = min(secondary_best_dist, distance)
                except:
                    continue
            
            # Both models must pass their thresholds
            primary_confidence = 1 - primary_best_dist
            secondary_confidence = 1 - secondary_best_dist
            
            primary_pass = primary_confidence >= CONFIG.primary_threshold
            secondary_pass = secondary_confidence >= CONFIG.secondary_threshold
            
            if primary_pass and secondary_pass:
                # Both models confirmed - use average confidence
                avg_confidence = (primary_confidence + secondary_confidence) / 2
                avg_distance = (primary_best_dist + secondary_best_dist) / 2
                
                if avg_confidence > (1 - best_primary_dist + 1 - best_secondary_dist) / 2:
                    best_primary_dist = primary_best_dist
                    best_secondary_dist = secondary_best_dist
                    best_user_idx = user_idx
                    
                    best_match = {
                        'user_idx': user_idx,
                        'distance': avg_distance,
                        'confidence': avg_confidence,
                        'primary_confidence': primary_confidence,
                        'secondary_confidence': secondary_confidence,
                        'primary_distance': primary_best_dist,
                        'secondary_distance': secondary_best_dist,
                        'threshold': (CONFIG.primary_threshold + CONFIG.secondary_threshold) / 2,
                        'user_info': user_info[user_idx]
                    }
                    
                    print(f"  ✓ 2-Factor Verified: {user_info[user_idx]['name']}")
                    print(f"      ArcFace: {primary_confidence:.2%}, Facenet: {secondary_confidence:.2%}")

        if best_match and best_user_idx != -1:
            user_id = user_info[best_user_idx]['id']
            if user_id not in self.recognition_history:
                self.recognition_history[user_id] = deque(maxlen=50)
            self.recognition_history[user_id].append(best_match['confidence'])
            log_recognition(
                user_id=user_id,
                confidence=best_match['confidence'],
                primary_confidence=best_match.get('primary_confidence'),
                secondary_confidence=best_match.get('secondary_confidence'),
                primary_distance=best_match.get('primary_distance'),
                secondary_distance=best_match.get('secondary_distance'),
                face_quality=face_quality,
                method="two-factor",
            )

        return best_match, []

# Initialize face recognition system
face_recognition_system = FaceRecognitionSystem()

# -------------------------------
# Runtime state is centralized under STATE
# -------------------------------

# -------------------------------
# Register or Recognize Face
# -------------------------------
def register_or_recognize_face(
    face_crop,
    face_id=None,
    allow_registration=False,
    detection_confidence=None,
    landmarks=None,
    precomputed_quality=None,
):
    if STATE.registration_in_progress:
        return None

    if precomputed_quality is None:
        quality_score, quality_status, quality_debug = assess_face_quality(
            face_crop,
            detection_confidence=detection_confidence,
            landmarks=landmarks,
        )
    else:
        quality_score, quality_status, quality_debug = precomputed_quality
    
    if quality_score < CONFIG.face_quality_threshold:
        print(f"  Skipping low quality face: {quality_score:.2f} ({quality_status})")
        return None
    
    print(
        f"  Face quality: {quality_score:.2f} ({quality_status}) "
        f"| sharpness={quality_debug.get('sharpness', 0.0):.1f} "
        f"| det={quality_debug.get('detection_confidence', 0.0):.2f}"
    )
    
    embeddings = extract_embedding_ensemble(face_crop)
    if not embeddings:
        print("  Failed to extract embeddings")
        return None
    
    best_match, all_distances = face_recognition_system.find_best_match(
        embeddings, STATE.all_user_embeddings, STATE.user_info, quality_score
    )
    
    if best_match:
        user_idx = best_match['user_idx']
        user_id = best_match['user_info']['id']

        STATE.all_user_embeddings[user_idx].setdefault(CONFIG.primary_model, []).append(embeddings[CONFIG.primary_model])
        STATE.all_user_embeddings[user_idx].setdefault(CONFIG.secondary_model, []).append(embeddings[CONFIG.secondary_model])

        timestamp = int(time.time() * 1000)
        user_folder = os.path.join(CONFIG.base_save_dir, best_match['user_info']['sr_code'])
        os.makedirs(user_folder, exist_ok=True)
        filename = os.path.join(user_folder, f"face_{timestamp}_learned.jpg")
        cv2.imwrite(filename, face_crop)

        conn = sqlite3.connect(CONFIG.db_path)
        c = conn.cursor()

        c.execute("SELECT embeddings, image_paths FROM users WHERE user_id = ?", (user_id,))
        existing_emb_blob, existing_paths_str = c.fetchone()
        existing_embeddings = pickle.loads(existing_emb_blob) if existing_emb_blob else {}
        existing_paths = existing_paths_str.split(';') if existing_paths_str else []

        existing_embeddings = merge_embeddings_by_model(existing_embeddings, embeddings)
        existing_paths.append(filename)

        updated_emb_blob = pickle.dumps(existing_embeddings)
        updated_paths_str = ';'.join(existing_paths)
        c.execute("""
            UPDATE users
            SET embeddings = ?, image_paths = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (updated_emb_blob, updated_paths_str, user_id))

        conn.commit()
        conn.close()

        total_emb = count_embeddings(STATE.all_user_embeddings[user_idx])
        print(f"✓ Learned new embedding for {best_match['user_info']['name']} "
              f"(total: {total_emb} embeddings across models)")

        STATE.recognized_user = {
            'name': best_match['user_info']['name'],
            'sr_code': best_match['user_info']['sr_code'],
            'course': '',
            'confidence': f"{best_match['confidence']:.2%}",
            'distance': f"{best_match['distance']:.4f}"
        }
        print(f"✓ Recognized: {STATE.recognized_user['name']} "
              f"(conf: {best_match['confidence']:.2%}, dist: {best_match['distance']:.4f})")
        STATE.registration_in_progress = False
        return True
    else:
        if allow_registration and not STATE.registration_in_progress and len(STATE.captured_faces_for_registration) < STATE.max_captures_for_registration:
            STATE.captured_faces_for_registration.append({
                'face_crop': face_crop,
                'embeddings': embeddings,
                'quality': quality_score
            })
            STATE.face_capture_count += 1
            print(f"  Captured face {STATE.face_capture_count}/{STATE.max_captures_for_registration} for registration")
            
            if STATE.face_capture_count >= STATE.max_captures_for_registration:
                STATE.pending_registration = STATE.captured_faces_for_registration.copy()
                STATE.registration_in_progress = True
                print(f"✗ New face detected - Ready for registration with {len(STATE.pending_registration)} samples")
        
        return False

def initialize_track_state(track_id, current_time):
    """Create or refresh cached recognition state for a tracker ID."""
    if track_id not in STATE.tracked_identities:
        STATE.tracked_identities[track_id] = {
            "recognized": False,
            "user": None,
            "last_seen": current_time,
            "last_recognition_time": 0.0,
        }
    else:
        STATE.tracked_identities[track_id]["last_seen"] = current_time
    return STATE.tracked_identities[track_id]

def cleanup_stale_tracks(current_time):
    """Remove old tracker states and stability buffers that are no longer visible."""
    stale_cutoff = current_time - CONFIG.track_stale_seconds
    stale_track_ids = [
        track_id for track_id, state in STATE.tracked_identities.items()
        if state.get("last_seen", 0.0) < stale_cutoff
    ]
    for track_id in stale_track_ids:
        STATE.tracked_identities.pop(track_id, None)
        STATE.face_stability_tracker.pop(track_id, None)
    return stale_track_ids

# -------------------------------
# Face stability checking
# -------------------------------
def check_face_stability(face_id, x1, y1, x2, y2):
    """Check if face has been stable for the required time"""
    current_time = time.time()
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    if face_id not in STATE.face_stability_tracker:
        STATE.face_stability_tracker[face_id] = {
            'positions': [(center_x, center_y)],
            'timestamps': [current_time],
            'stable_since': None
        }
        return False

    tracker = STATE.face_stability_tracker[face_id]

    last_x, last_y = tracker['positions'][-1]
    distance = ((center_x - last_x) ** 2 + (center_y - last_y) ** 2) ** 0.5

    if distance <= CONFIG.position_tolerance:
        tracker['positions'].append((center_x, center_y))
        tracker['timestamps'].append(current_time)

        cutoff_time = current_time - 5.0
        valid_indices = [i for i, t in enumerate(tracker['timestamps']) if t >= cutoff_time]
        tracker['positions'] = [tracker['positions'][i] for i in valid_indices]
        tracker['timestamps'] = [tracker['timestamps'][i] for i in valid_indices]

        if len(tracker['timestamps']) >= 2:
            stable_duration = tracker['timestamps'][-1] - tracker['timestamps'][0]
            if stable_duration >= CONFIG.stability_time_required:
                if tracker['stable_since'] is None:
                    tracker['stable_since'] = current_time
                return True
    else:
        tracker['positions'] = [(center_x, center_y)]
        tracker['timestamps'] = [current_time]
        tracker['stable_since'] = None

    return False

# -------------------------------
# CCTV Stream Face Recognition
# -------------------------------
def connect_to_cctv_stream(stream_url, frame_width=640, frame_height=480, target_fps=30):
    """Connect to CCTV stream or webcam"""
    print(f"Attempting to connect to: {stream_url}")
    
    # If it's a webcam (numeric index), use DirectShow on Windows for lower latency.
    if isinstance(stream_url, str) and stream_url.isdigit():
        cam_index = int(stream_url)
        if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
            cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(cam_index)
    else:
        # Try different backend for RTSP streams
        cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            # Try default backend
            cap = cv2.VideoCapture(stream_url)
        
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FPS, target_fps)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
        print("✓ Successfully connected!")
        return cap
    else:
        print("✗ Failed to connect")
        return None

def process_cctv_stream(stream_url, frame_width=1280, frame_height=720):
    """Process CCTV stream for face recognition"""
    camera = connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
    
    if camera is None:
        return
    
    print("\n" + "="*50)
    print("CCTV FACE RECOGNITION SYSTEM")
    print("="*50)
    print("Press 'q' to quit")
    print("Press 'r' to reset recognition status")
    print("Press 'n' to check/add a new face (manual)")
    print("="*50)
    
    fps_counter = 0
    fps_start_time = time.time()
    current_fps = 0
    registration_prompted = False
    
    while True:
        success, frame = camera.read()
        if not success:
            print("✗ Lost connection to CCTV stream. Reconnecting...")
            camera = connect_to_cctv_stream(stream_url, frame_width, frame_height, target_fps=30)
            if camera is None:
                break
            continue

        frame = cv2.resize(frame, (frame_width, frame_height))
        current_time = time.time()

        results = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=0.3,
            imgsz=768,
            device=yolo_device,
            verbose=False,
        )

        face_crops = []
        face_qualities = []
        stable_faces = []

        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detection_confidence = float(box.conf[0]) if box.conf is not None else None

                if (x2 - x1) < CONFIG.min_face_size or (y2 - y1) < CONFIG.min_face_size:
                    continue

                face_crop = frame[y1:y2, x1:x2]
                face_crops.append(face_crop)

                quality_score, quality_status, quality_debug = assess_face_quality(
                    face_crop,
                    detection_confidence=detection_confidence,
                )
                face_qualities.append((quality_score, quality_status))

                track_id = int(box.id[0]) if box.id is not None else None
                face_id = track_id
                if face_id is not None:
                    initialize_track_state(face_id, current_time)

                is_stable = False
                if face_id is not None:
                    is_stable = check_face_stability(face_id, x1, y1, x2, y2)

                if is_stable:
                    stable_faces.append((
                        face_crop,
                        face_id,
                        detection_confidence,
                        (quality_score, quality_status, quality_debug),
                    ))

                if is_stable:
                    if quality_score >= CONFIG.face_quality_good_threshold:
                        color = (0, 255, 0)
                    elif quality_score >= CONFIG.face_quality_threshold:
                        color = (0, 255, 255)
                    else:
                        color = (0, 0, 255)
                else:
                    color = (128, 128, 128)

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                track_text = f"T{face_id}" if face_id is not None else "T?"
                stability_text = "NO-ID" if face_id is None else ("STABLE" if is_stable else "MOVING")
                cv2.putText(frame, f"{track_text} {stability_text} Q:{quality_score:.1f}",
                           (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
                           0.5, color, 1)

                track_state = STATE.tracked_identities.get(face_id) if face_id is not None else None
                if track_state and track_state.get("recognized") and track_state.get("user"):
                    cached_user = track_state["user"]
                    confidence_text = cached_user.get("confidence")
                    identity_text = (
                        f"{cached_user['name']} ({confidence_text})"
                        if confidence_text else cached_user["name"]
                    )
                    identity_color = (0, 255, 0)
                elif track_state and track_state.get("last_recognition_time", 0.0) > 0.0:
                    identity_text = "Unknown"
                    identity_color = (0, 165, 255)
                else:
                    identity_text = "Untracked" if face_id is None else "Tracking"
                    identity_color = (180, 180, 180)

                label_y = min(y2 + 15, frame_height - 10)
                cv2.putText(frame, identity_text,
                           (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX,
                           0.5, identity_color, 1)

        stale_track_ids = cleanup_stale_tracks(current_time)
        if STATE.manual_registration_track_id in stale_track_ids:
            STATE.manual_registration_active = False
            STATE.manual_registration_track_id = None
            STATE.captured_faces_for_registration = []
            STATE.face_capture_count = 0
            print("Manual registration track lost. Capture canceled.")

        for face_crop, face_id, detection_confidence, quality_tuple in stable_faces:
            if face_id is None or STATE.registration_in_progress:
                continue

            track_state = initialize_track_state(face_id, current_time)

            if STATE.manual_registration_requested and not STATE.manual_registration_active and track_state.get("recognized"):
                print("Face already in database. Manual registration canceled.")
                STATE.manual_registration_requested = False
                continue

            if track_state.get("recognized"):
                continue

            last_attempt = track_state.get("last_recognition_time", 0.0)
            if (current_time - last_attempt) < CONFIG.recognition_cooldown_seconds:
                continue

            track_state["last_recognition_time"] = current_time
            track_state["last_seen"] = current_time
            
            if STATE.manual_registration_requested and not STATE.manual_registration_active:
                result = register_or_recognize_face(
                    face_crop,
                    face_id,
                    allow_registration=False,
                    detection_confidence=detection_confidence,
                    precomputed_quality=quality_tuple,
                )
                if result is None:
                    continue
                if result:
                    track_state.update({
                        "recognized": True,
                        "user": dict(STATE.recognized_user) if STATE.recognized_user else None,
                        "last_seen": current_time,
                        "last_recognition_time": current_time,
                    })
                    print("Face already in database. Manual registration canceled.")
                    STATE.manual_registration_requested = False
                else:
                    track_state.update({
                        "recognized": False,
                        "user": None,
                        "last_seen": current_time,
                        "last_recognition_time": current_time,
                    })
                    print("Unknown face. Capturing 3 samples for registration...")
                    STATE.manual_registration_requested = False
                    STATE.manual_registration_active = True
                    STATE.manual_registration_track_id = face_id
                    STATE.captured_faces_for_registration = []
                    STATE.face_capture_count = 0
                    register_or_recognize_face(
                        face_crop,
                        face_id,
                        allow_registration=True,
                        detection_confidence=detection_confidence,
                        precomputed_quality=quality_tuple,
                    )
                    if STATE.registration_in_progress:
                        STATE.manual_registration_active = False
                        STATE.manual_registration_track_id = None
                continue

            if STATE.manual_registration_active and STATE.manual_registration_track_id is not None and face_id != STATE.manual_registration_track_id:
                continue

            allow_registration = STATE.manual_registration_active and (face_id == STATE.manual_registration_track_id)
            result = register_or_recognize_face(
                face_crop,
                face_id,
                allow_registration=allow_registration,
                detection_confidence=detection_confidence,
                precomputed_quality=quality_tuple,
            )

            if result is True:
                track_state.update({
                    "recognized": True,
                    "user": dict(STATE.recognized_user) if STATE.recognized_user else None,
                    "last_seen": current_time,
                    "last_recognition_time": current_time,
                })
            elif result is False:
                track_state.update({
                    "recognized": False,
                    "user": None,
                    "last_seen": current_time,
                    "last_recognition_time": current_time,
                })

            if STATE.manual_registration_active:
                if result is True:
                    STATE.manual_registration_active = False
                    STATE.manual_registration_track_id = None
                    STATE.captured_faces_for_registration = []
                    STATE.face_capture_count = 0
                    print("Face already in database. Manual registration canceled.")
                elif STATE.registration_in_progress:
                    STATE.manual_registration_active = False
                    STATE.manual_registration_track_id = None

        if STATE.registration_in_progress and STATE.pending_registration and not registration_prompted:
            registration_prompted = True
            handle_registration()
            STATE.recognized_user = None
            if not STATE.registration_in_progress:
                registration_prompted = False

        # Display thumbnails with quality info
        for i, (crop, (quality_score, quality_status)) in enumerate(zip(face_crops[:5], face_qualities[:5])):
            crop_h, crop_w = crop.shape[:2]
            scale = 80 / crop_h
            thumbnail = cv2.resize(crop, (int(crop_w * scale), 80))
            x_start = 10 + i * 90
            x_end = min(x_start + thumbnail.shape[1], frame_width)
            y_start = 80
            y_end = min(y_start + thumbnail.shape[0], frame_height)
            frame[y_start:y_end, x_start:x_end] = thumbnail[:y_end-y_start, :x_end-x_start]
            
            cv2.putText(frame, f"{quality_score:.1f}",
                       (x_start, y_end + 15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                       (255, 255, 255), 1)

        # Calculate FPS
        fps_counter += 1
        if time.time() - fps_start_time >= 1.0:
            current_fps = fps_counter
            fps_counter = 0
            fps_start_time = time.time()

        # Display refactored HUD
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame_width, 70), (0, 0, 0), -1)
        cv2.rectangle(overlay, (0, frame_height - 50), (frame_width, frame_height), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

        # Top-left: quick controls
        cv2.putText(frame, "Controls: [N] New User  [R] Reset  [Q] Quit",
                   (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.putText(frame, f"DB Users: {STATE.user_count}   FPS: {current_fps}",
                   (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

        # Bottom status line
        if STATE.recognized_user:
            status_text = f"Recognized: {STATE.recognized_user['name']} ({STATE.recognized_user['confidence']})"
            status_color = (0, 255, 0)
        elif STATE.registration_in_progress:
            status_text = f"Registration ready - {STATE.face_capture_count}/{STATE.max_captures_for_registration} samples"
            status_color = (0, 165, 255)
        elif STATE.manual_registration_active:
            status_text = f"Manual capture in progress: {STATE.face_capture_count}/{STATE.max_captures_for_registration}"
            status_color = (0, 165, 255)
        elif STATE.manual_registration_requested:
            status_text = "Manual check requested - Hold still"
            status_color = (0, 165, 255)
        else:
            status_text = "Scanning for faces..."
            status_color = (255, 255, 255)

        cv2.putText(frame, status_text, (10, frame_height - 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.65, status_color, 2)

        cv2.imshow('CCTV Face Recognition', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("\nShutting down...")
            break
        elif key == ord('r'):
            STATE.recognized_user = None
            STATE.manual_registration_requested = False
            STATE.manual_registration_active = False
            STATE.manual_registration_track_id = None
            STATE.captured_faces_for_registration = []
            STATE.face_capture_count = 0
            registration_prompted = False
            STATE.tracked_identities.clear()
            STATE.face_stability_tracker.clear()
            print("Recognition status reset")
        elif key == ord('n'):
            if STATE.registration_in_progress:
                print("Registration already in progress. Finish it before starting a new one.")
            else:
                STATE.manual_registration_requested = True
                STATE.manual_registration_active = False
                STATE.manual_registration_track_id = None
                STATE.captured_faces_for_registration = []
                STATE.face_capture_count = 0
                registration_prompted = False
                print("Manual new-user check requested. Hold still for recognition...")

    camera.release()
    cv2.destroyAllWindows()

# -------------------------------
# Handle Registration (CLI)
# -------------------------------
def handle_registration():
    """Handle new user registration via CLI"""
    if not STATE.pending_registration:
        print("No pending registration")
        return

    print("\n" + "="*50)
    print("NEW USER REGISTRATION")
    print("="*50)
    print(f"Captured {len(STATE.pending_registration)} face samples")
    
    name = input("Enter full name: ").strip()
    sr_code = input("Enter SR Code: ").strip()
    course = input("Enter course: ").strip()

    if not name or not sr_code or not course:
        print("Error: All fields are required!")
        return

    # Check if SR code exists
    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE sr_code = ?", (sr_code,))
    existing = c.fetchone()
    conn.close()
    
    if existing:
        print(f"Warning: SR Code {sr_code} already registered to {existing[0]}")
        choice = input("Update existing user? (y/n): ").lower()
        if choice != 'y':
            return

    all_embeddings = {}
    image_paths = []
    
    for i, face_data in enumerate(STATE.pending_registration):
        timestamp = int(time.time() * 1000)
        user_folder = os.path.join(CONFIG.base_save_dir, sr_code)
        os.makedirs(user_folder, exist_ok=True)
        filename = os.path.join(user_folder, f"face_{timestamp}_{i}.jpg")
        cv2.imwrite(filename, face_data['face_crop'])
        image_paths.append(filename)
        
        if face_data['embeddings']:
            all_embeddings = merge_embeddings_by_model(all_embeddings, face_data['embeddings'])
    
    if all_embeddings:
        user_id = save_user_with_multiple_embeddings(all_embeddings, image_paths, name, sr_code, course)
        
        STATE.all_user_embeddings.append(all_embeddings)
        STATE.user_info.append({
            'id': user_id,
            'name': name,
            'sr_code': sr_code
        })
        STATE.user_count = len(STATE.user_info)
        
        total_emb = count_embeddings(all_embeddings)
        print(f"✓ Registered {name} with {total_emb} embeddings across models")

    STATE.pending_registration = None
    STATE.captured_faces_for_registration = []
    STATE.face_capture_count = 0
    STATE.registration_in_progress = False
    STATE.manual_registration_requested = False
    STATE.manual_registration_active = False
    STATE.manual_registration_track_id = None

# -------------------------------
# Main CLI Interface
# -------------------------------
def main_menu():
    """Main CLI menu for the system"""
    while True:
        print("\n" + "="*50)
        print("CCTV FACE RECOGNITION SYSTEM")
        print("="*50)
        print(f"Users in database: {STATE.user_count}")
        print(f"Models: {CONFIG.primary_model} + {CONFIG.secondary_model}")
        print("="*50)
        print("1. Start CCTV Face Recognition")
        print("2. Register New User (Webcam)")
        print("3. List Registered Users")
        print("4. Delete User")
        print("5. View Statistics")
        print("6. Reset Database")
        print("7. Exit")
        print("="*50)
        
        choice = input("\nEnter your choice (1-7): ").strip()
        
        if choice == '1':
            stream_url = input("Enter CCTV stream URL (or camera index, press Enter for 0): ").strip()
            if not stream_url:
                stream_url = "0"  # Use default webcam
            process_cctv_stream(stream_url)
            
            # Handle registration if needed
            if STATE.registration_in_progress:
                handle_registration()
                
        elif choice == '2':
            print("\nStarting webcam registration...")
            subprocess.run([sys.executable, "register_fixed.py"])
            # Reload embeddings after registration
            STATE.all_user_embeddings, STATE.user_info = load_all_embeddings()
            STATE.user_count = len(STATE.user_info)
            
        elif choice == '3':
            print("\n" + "="*50)
            print("REGISTERED USERS")
            print("="*50)
            if not STATE.user_info:
                print("No users registered")
            else:
                for info in STATE.user_info:
                    print(f"ID: {info['id']:3d} | Name: {info['name']:20s} | SR Code: {info['sr_code']}")
            print("="*50)
            
        elif choice == '4':
            try:
                user_id = int(input("Enter User ID to delete (0 to cancel): "))
                if user_id == 0:
                    continue
                    
                conn = sqlite3.connect(CONFIG.db_path)
                c = conn.cursor()
                c.execute("SELECT name, sr_code FROM users WHERE user_id = ?", (user_id,))
                user = c.fetchone()
                
                if not user:
                    print(f"User ID {user_id} not found")
                    conn.close()
                    continue
                    
                print(f"Delete user: {user[0]} ({user[1]})?")
                confirm = input("Confirm (y/n): ").lower()
                
                if confirm == 'y':
                    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                    conn.commit()
                    print("User deleted")
                    
                    # Reload embeddings
                    STATE.all_user_embeddings, STATE.user_info = load_all_embeddings()
                    STATE.user_count = len(STATE.user_info)
                    
                conn.close()
            except ValueError:
                print("Invalid input")
                
        elif choice == '5':
            conn = sqlite3.connect(CONFIG.db_path)
            c = conn.cursor()
            c.execute("""
                SELECT u.user_id, u.name, u.sr_code, u.embedding_dim, u.embeddings,
                       COUNT(r.log_id) as recognitions,
                       AVG(r.confidence) as avg_confidence,
                       MAX(r.confidence) as best_confidence,
                       MAX(r.timestamp) as last_seen
                FROM users u
                LEFT JOIN recognition_log r ON u.user_id = r.user_id
                GROUP BY u.user_id
                ORDER BY recognitions DESC
            """)
            stats = c.fetchall()
            
            print("\n" + "="*50)
            print("RECOGNITION STATISTICS")
            print("="*50)
            print(f"Method: Two-Factor ({CONFIG.primary_model} + {CONFIG.secondary_model})")
            print(
                f"Thresholds: {CONFIG.primary_model}>={CONFIG.primary_threshold:.2f}, "
                f"{CONFIG.secondary_model}>={CONFIG.secondary_threshold:.2f}"
            )
            print("-"*50)
            if not stats:
                print("No recognition data")
            else:
                for user_id, name, sr_code, embedding_dim, emb_blob, rec_count, avg_conf, best_conf, last_seen in stats:
                    embeddings_by_model = {}
                    if emb_blob:
                        try:
                            embeddings_by_model = normalize_embeddings_by_model(pickle.loads(emb_blob))
                        except Exception:
                            embeddings_by_model = {}

                    model_counts = {
                        model_name: len(emb_list)
                        for model_name, emb_list in embeddings_by_model.items()
                        if emb_list
                    }
                    total_embeddings = sum(model_counts.values())
                    model_counts_str = (
                        ", ".join(f"{model}:{count}" for model, count in model_counts.items())
                        if model_counts else "N/A"
                    )

                    c.execute(
                        """
                        SELECT timestamp, method, primary_confidence, secondary_confidence,
                               primary_distance, secondary_distance, face_quality
                        FROM recognition_log
                        WHERE user_id = ?
                        ORDER BY timestamp DESC, log_id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    latest = c.fetchone()

                    avg_conf_num = _coerce_float(avg_conf)
                    best_conf_num = _coerce_float(best_conf)
                    avg = f"{avg_conf_num:.2%}" if avg_conf_num is not None else "N/A"
                    best = f"{best_conf_num:.2%}" if best_conf_num is not None else "N/A"
                    last_seen_str = last_seen if last_seen else "Never"

                    print(f"Name: {name} ({sr_code})")
                    print(f"  Recognitions: {rec_count} | Avg Confidence: {avg} | Best Confidence: {best}")
                    print(f"  Embeddings: total={total_embeddings}, dim={embedding_dim}, by_model=[{model_counts_str}]")
                    print(f"  Last Recognition: {last_seen_str}")

                    if latest:
                        ts, method, p_conf, s_conf, p_dist, s_dist, face_quality = latest
                        p_conf = _coerce_float(p_conf)
                        s_conf = _coerce_float(s_conf)
                        p_dist = _coerce_float(p_dist)
                        s_dist = _coerce_float(s_dist)
                        face_quality = _coerce_float(face_quality)

                        p_conf_s = f"{p_conf:.2%}" if p_conf is not None else "N/A"
                        s_conf_s = f"{s_conf:.2%}" if s_conf is not None else "N/A"
                        p_dist_s = f"{p_dist:.4f}" if p_dist is not None else "N/A"
                        s_dist_s = f"{s_dist:.4f}" if s_dist is not None else "N/A"
                        quality_s = f"{face_quality:.2f}" if face_quality is not None else "N/A"
                        print(
                            "  Latest Match Details: "
                            f"method={method or 'N/A'}, "
                            f"{CONFIG.primary_model}_conf={p_conf_s}, {CONFIG.secondary_model}_conf={s_conf_s}, "
                            f"{CONFIG.primary_model}_dist={p_dist_s}, {CONFIG.secondary_model}_dist={s_dist_s}, "
                            f"face_quality={quality_s}, timestamp={ts}"
                        )
                    else:
                        print("  Latest Match Details: N/A")
                    print("-"*50)

            conn.close()
            print("="*50)
            
        elif choice == '6':
            print("\n⚠️  WARNING: This will delete ALL users and data!")
            confirm = input("Type 'YES' to confirm: ")
            if confirm == 'YES':
                conn = sqlite3.connect(CONFIG.db_path)
                c = conn.cursor()
                c.execute("DELETE FROM users")
                c.execute("DELETE FROM recognition_log")
                conn.commit()
                conn.close()
                
                import shutil
                if os.path.exists(CONFIG.base_save_dir):
                    shutil.rmtree(CONFIG.base_save_dir)
                
                STATE.all_user_embeddings = []
                STATE.user_info = []
                STATE.user_count = 0
                print("Database reset complete")
            else:
                print("Reset cancelled")
                
        elif choice == '7':
            print("\nExiting... Goodbye!")
            break
        else:
            print("Invalid choice")

# -------------------------------
# Run the system
# -------------------------------
if __name__ == "__main__":
    init_db()
    log_header("CCTV Face Recognition System - Initialization")
    log_step(f"Database: {CONFIG.db_path}")
    log_step(f"Face models: {CONFIG.primary_model} + {CONFIG.secondary_model}")
    log_step(f"Base threshold: {CONFIG.base_threshold}")
    log_step(f"Users in database: {STATE.user_count}")
    
    main_menu()

