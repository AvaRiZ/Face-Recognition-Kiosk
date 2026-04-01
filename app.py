import argparse
import sys
import subprocess
import cv2
import numpy as np
import os
import sys
import time
from deepface import DeepFace  # @UnresolvedImport
import pickle
import faiss  # pyright: ignore[reportMissingImports]
import mediapipe as mp
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import statistics
import threading 
from flask import Flask, redirect, session, url_for
from auth import (
    init_auth_db,
    login_required,
    role_required,
)
from routes.routes import create_routes_blueprint, init_imported_logs_table
from routes.auth_routes import create_auth_blueprint
from routes.profile_routes import create_profile_blueprint
from services.face_service import render_markdown_as_html
from services.staff_service import ensure_profile_upload_dir, save_profile_image
import torch
import tensorflow as tf
from ultralytics import YOLO
from config import AppConfig
from db import connect as db_connect, table_columns

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

DeepFace = None

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

@dataclass
class AppState:
    all_user_embeddings: list = field(default_factory=list)
    user_info: list = field(default_factory=list)
    faiss_indexes: dict = field(default_factory=dict)
    faiss_metadata: dict = field(default_factory=dict)
    faiss_id_maps: dict = field(default_factory=dict)
    faiss_user_vector_ids: dict = field(default_factory=dict)
    faiss_next_id: dict = field(default_factory=dict)
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
    embedding_candidates: dict = field(default_factory=dict)
    embedding_consistency_tracker: dict = field(default_factory=dict)
    last_embedding_commit_time: float = 0.0


CONFIG = AppConfig()
STATE = AppState()
MP_FACE_MESH = None
MEDIAPIPE_FACEMESH_UNAVAILABLE = False
QUALITY_CLAHE = None
QUALITY_GAMMA_LUT = None
ALIGNMENT_SOURCE_LAST_PRINT_TS = 0.0
ALIGNMENT_SOURCE_LAST_PRINT_VALUE = None
APP_INITIALIZED = False
yolo_device = "cpu"
model = None


def initialize_runtime():
    """Initialize GPU config, models, storage, and indexes once."""
    global APP_INITIALIZED, DeepFace, model, yolo_device
    if APP_INITIALIZED:
        return

    configure_devices(torch_device_index=0, tf_use_gpu=True)

    from deepface import DeepFace as _DeepFace
    DeepFace = _DeepFace

    log_gpu_info()

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
    log_step(f"Two-Factor Verification: {CONFIG.primary_model} + {CONFIG.secondary_model}")

    os.makedirs(CONFIG.base_save_dir, exist_ok=True)

    init_db()
    STATE.all_user_embeddings, STATE.user_info = load_all_embeddings()
    STATE.user_count = len(STATE.user_info)
    refresh_faiss()

    APP_INITIALIZED = True

# -------------------------------
# Database setup (SQLite) with improved schema
# -------------------------------
def init_db():
    conn = db_connect(CONFIG.db_path)
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
            embedding_metadata BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            archived_at TIMESTAMP
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
    existing_columns = table_columns(conn, "recognition_log")
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

    c.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in c.fetchall()}
    if "embedding_metadata" not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN embedding_metadata BLOB")
    
    conn.commit()
    conn.close()

def save_user_with_multiple_embeddings(embeddings_by_model, image_paths, name, sr_code, course):
    conn = db_connect(CONFIG.db_path)
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
        if getattr(conn, "dialect", "sqlite") == "postgres":
            c.execute("""
                INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim) 
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING user_id
            """, (name, sr_code, course, embeddings_blob, ';'.join(image_paths), embedding_dim))
            user_id = c.fetchone()[0]
        else:
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
    conn = db_connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute(
        """
        SELECT user_id, name, sr_code, embeddings
        FROM users
        WHERE archived_at IS NULL
        """
    )
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


def _normalize_embedding_vector(embedding):
    """Prepare a single embedding vector for FAISS (float32 + unit norm)."""
    if embedding is None:
        return None
    arr = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if arr.ndim != 1 or arr.size == 0:
        return None

    norm = np.linalg.norm(arr)
    if norm <= 0:
        return None
    arr = arr / norm
    return arr


def _ensure_faiss_support_state():
    """Ensure all FAISS support maps exist for configured models."""
    if not isinstance(STATE.faiss_indexes, dict):
        STATE.faiss_indexes = {}
    if not isinstance(STATE.faiss_metadata, dict):
        STATE.faiss_metadata = {}
    if not isinstance(STATE.faiss_id_maps, dict):
        STATE.faiss_id_maps = {}
    if not isinstance(STATE.faiss_user_vector_ids, dict):
        STATE.faiss_user_vector_ids = {}
    if not isinstance(STATE.faiss_next_id, dict):
        STATE.faiss_next_id = {}

    for model_name in CONFIG.models:
        STATE.faiss_indexes.setdefault(model_name, None)
        STATE.faiss_metadata.setdefault(model_name, [])
        STATE.faiss_id_maps.setdefault(model_name, {})
        STATE.faiss_user_vector_ids.setdefault(model_name, {})
        STATE.faiss_next_id.setdefault(model_name, 0)


def _faiss_create_index(dim):
    """Create an ID-addressable FAISS index while keeping IndexFlatIP as backend."""
    base = faiss.IndexFlatIP(int(dim))
    return faiss.IndexIDMap2(base)


def _faiss_rebuild_with_reason(reason):
    log_step(f"FAISS map mismatch detected. Rebuilding all indexes. Reason: {reason}", status="WARN")
    refresh_faiss()


def _get_user_index_by_id(user_id):
    for idx, info in enumerate(STATE.user_info):
        if isinstance(info, dict) and info.get('id') == user_id:
            return idx
    return -1


def build_faiss_indexes(all_user_embeddings, user_info):
    """Build per-model FAISS IndexFlatIP instances and vector metadata mappings."""
    faiss_indexes = {model_name: None for model_name in CONFIG.models}
    faiss_metadata = {model_name: [] for model_name in CONFIG.models}
    faiss_id_maps = {model_name: {} for model_name in CONFIG.models}
    faiss_user_vector_ids = {model_name: {} for model_name in CONFIG.models}
    faiss_next_id = {model_name: 0 for model_name in CONFIG.models}

    user_id_by_index = {
        idx: info.get('id')
        for idx, info in enumerate(user_info)
        if isinstance(info, dict) and info.get('id') is not None
    }

    for model_name in CONFIG.models:
        vectors = []
        metadata = []
        faiss_ids = []

        for user_idx, user_embeddings_by_model in enumerate(all_user_embeddings):
            if not isinstance(user_embeddings_by_model, dict):
                continue

            user_id = user_id_by_index.get(user_idx)
            if user_id is None:
                continue

            normalized_models = normalize_embeddings_by_model(user_embeddings_by_model)
            model_embeddings = normalized_models.get(model_name, [])
            for embedding_id, embedding in enumerate(model_embeddings):
                vec = _normalize_embedding_vector(embedding)
                if vec is None:
                    continue
                faiss_id = faiss_next_id[model_name]
                faiss_next_id[model_name] += 1

                vectors.append(vec)
                metadata.append((user_id, embedding_id))
                faiss_ids.append(faiss_id)
                faiss_id_maps[model_name][faiss_id] = (user_id, embedding_id)
                faiss_user_vector_ids[model_name].setdefault(user_id, set()).add(faiss_id)

        if vectors:
            vector_matrix = np.vstack(vectors).astype(np.float32, copy=False)
            index = _faiss_create_index(vector_matrix.shape[1])
            id_array = np.array(faiss_ids, dtype=np.int64)
            index.add_with_ids(vector_matrix, id_array)
            faiss_indexes[model_name] = index
            faiss_metadata[model_name] = metadata

            log_step(
                f"FAISS index built for {model_name}: {index.ntotal} vectors, dim={index.d}"
            )
        else:
            faiss_metadata[model_name] = []
            log_step(f"FAISS index built for {model_name}: 0 vectors", status="WARN")

    return faiss_indexes, faiss_metadata, faiss_id_maps, faiss_user_vector_ids, faiss_next_id


def refresh_faiss():
    """Refresh FAISS indexes from in-memory STATE embeddings after data changes."""
    (
        STATE.faiss_indexes,
        STATE.faiss_metadata,
        STATE.faiss_id_maps,
        STATE.faiss_user_vector_ids,
        STATE.faiss_next_id,
    ) = build_faiss_indexes(
        STATE.all_user_embeddings,
        STATE.user_info,
    )
    _ensure_faiss_support_state()


def add_embeddings_incremental(user_id, embeddings_by_model, start_offsets=None):
    """Incrementally add vectors to FAISS for one user.

    Falls back to full rebuild on index/map mismatches.
    """
    _ensure_faiss_support_state()
    normalized = normalize_embeddings_by_model(embeddings_by_model)
    if not normalized:
        return True

    if start_offsets is None:
        start_offsets = {model_name: 0 for model_name in CONFIG.models}

    try:
        for model_name in CONFIG.models:
            model_embeddings = normalized.get(model_name, [])
            if not model_embeddings:
                continue

            prepared_vectors = []
            prepared_ids = []
            prepared_meta = []

            for local_idx, embedding in enumerate(model_embeddings):
                vec = _normalize_embedding_vector(embedding)
                if vec is None:
                    continue

                faiss_id = int(STATE.faiss_next_id.get(model_name, 0))
                STATE.faiss_next_id[model_name] = faiss_id + 1
                embedding_id = int(start_offsets.get(model_name, 0)) + local_idx

                prepared_vectors.append(vec)
                prepared_ids.append(faiss_id)
                prepared_meta.append((faiss_id, user_id, embedding_id))

            if not prepared_vectors:
                continue

            matrix = np.vstack(prepared_vectors).astype(np.float32, copy=False)
            index = STATE.faiss_indexes.get(model_name)

            if index is None:
                index = _faiss_create_index(matrix.shape[1])
                STATE.faiss_indexes[model_name] = index

            if index.d != matrix.shape[1]:
                _faiss_rebuild_with_reason(
                    f"dimension mismatch on incremental add for {model_name} (index={index.d}, incoming={matrix.shape[1]})"
                )
                return False

            id_array = np.array(prepared_ids, dtype=np.int64)
            index.add_with_ids(matrix, id_array)

            for faiss_id, uid, emb_id in prepared_meta:
                STATE.faiss_id_maps[model_name][faiss_id] = (uid, emb_id)
                STATE.faiss_user_vector_ids[model_name].setdefault(uid, set()).add(faiss_id)
                STATE.faiss_metadata[model_name].append((uid, emb_id))

        return True
    except Exception as e:
        _faiss_rebuild_with_reason(f"incremental add exception: {e}")
        return False


def remove_user_incremental(user_id):
    """Incrementally remove all vectors for one user from FAISS.

    Falls back to full rebuild on index/map mismatches.
    """
    _ensure_faiss_support_state()

    try:
        for model_name in CONFIG.models:
            user_id_set_map = STATE.faiss_user_vector_ids.get(model_name, {})
            ids = sorted(list(user_id_set_map.get(user_id, set())))
            if not ids:
                continue

            index = STATE.faiss_indexes.get(model_name)
            if index is None:
                _faiss_rebuild_with_reason(f"missing index during remove for {model_name}")
                return False

            id_array = np.array(ids, dtype=np.int64)
            removed_count = int(index.remove_ids(id_array))
            if removed_count != len(ids):
                _faiss_rebuild_with_reason(
                    f"remove count mismatch for {model_name} (removed={removed_count}, expected={len(ids)})"
                )
                return False

            for vector_id in ids:
                STATE.faiss_id_maps.get(model_name, {}).pop(vector_id, None)

            user_id_set_map.pop(user_id, None)

        return True
    except Exception as e:
        _faiss_rebuild_with_reason(f"incremental remove exception: {e}")
        return False

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

    conn = db_connect(CONFIG.db_path)
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


def _get_quality_clahe():
    global QUALITY_CLAHE
    if QUALITY_CLAHE is None:
        tile = max(int(CONFIG.quality_clahe_tile_grid), 2)
        QUALITY_CLAHE = cv2.createCLAHE(
            clipLimit=float(CONFIG.quality_clahe_clip_limit),
            tileGridSize=(tile, tile),
        )
    return QUALITY_CLAHE


def _get_quality_gamma_lut():
    global QUALITY_GAMMA_LUT
    gamma = float(CONFIG.quality_gamma)
    if QUALITY_GAMMA_LUT is None or abs(gamma - 1.0) > 1e-6:
        inv_gamma = 1.0 / max(gamma, 1e-6)
        table = np.array(
            [(i / 255.0) ** inv_gamma * 255 for i in range(256)],
            dtype=np.uint8,
        )
        QUALITY_GAMMA_LUT = table
    return QUALITY_GAMMA_LUT


def _preprocess_face_for_embedding(face_crop):
    """Apply CLAHE + gamma correction only after quality scoring has already passed."""
    if face_crop is None or face_crop.size == 0:
        return face_crop

    processed = face_crop.copy()
    if len(processed.shape) == 2:
        processed = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)

    clahe = _get_quality_clahe()
    lab = cv2.cvtColor(processed, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    l_chan = clahe.apply(l_chan)
    processed = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)

    lut = _get_quality_gamma_lut()
    processed = cv2.LUT(processed, lut)

    return processed


def _get_face_mesh():
    global MP_FACE_MESH, MEDIAPIPE_FACEMESH_UNAVAILABLE
    if MEDIAPIPE_FACEMESH_UNAVAILABLE:
        return None

    if MP_FACE_MESH is None:
        try:
            model_path = str(CONFIG.mediapipe_face_landmarker_model_path)
            if not os.path.isfile(model_path):
                raise FileNotFoundError(
                    f"FaceLandmarker model not found at '{model_path}'. "
                    "Download face_landmarker.task and place it there."
                )

            from mediapipe.tasks import python as mp_tasks_python
            from mediapipe.tasks.python import vision as mp_tasks_vision

            base_options = mp_tasks_python.BaseOptions(model_asset_path=model_path)
            options = mp_tasks_vision.FaceLandmarkerOptions(
                base_options=base_options,
                running_mode=mp_tasks_vision.RunningMode.IMAGE,
                num_faces=int(CONFIG.mediapipe_max_faces),
                min_face_detection_confidence=float(CONFIG.mediapipe_min_detection_confidence),
                min_face_presence_confidence=float(CONFIG.mediapipe_min_tracking_confidence),
                min_tracking_confidence=float(CONFIG.mediapipe_min_tracking_confidence),
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
            MP_FACE_MESH = mp_tasks_vision.FaceLandmarker.create_from_options(options)
        except Exception as e:
            MEDIAPIPE_FACEMESH_UNAVAILABLE = True
            CONFIG.use_mediapipe_landmarks = False
            print(
                "Warning: MediaPipe FaceLandmarker unavailable; falling back to approx alignment. "
                f"Reason: {e}"
            )
            return None

    return MP_FACE_MESH


def _bbox_iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    denom = max(area_a + area_b - inter_area, 1e-6)
    return inter_area / denom


def _extract_mediapipe_faces(frame_bgr):
    """Return list of mediapipe faces: [{'bbox': (x1,y1,x2,y2), 'landmarks': {...}}]."""
    if not CONFIG.use_mediapipe_landmarks:
        return []
    if frame_bgr is None or frame_bgr.size == 0:
        return []

    h, w = frame_bgr.shape[:2]
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    face_landmarker = _get_face_mesh()
    if face_landmarker is None:
        return []

    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    results = face_landmarker.detect(mp_image)
    if not results.face_landmarks:
        return []

    # MediaPipe FaceMesh landmark indices (stable, common references)
    left_eye_idx = (33, 133)
    right_eye_idx = (362, 263)
    nose_idx = 1
    mouth_left_idx = 61
    mouth_right_idx = 291

    faces = []
    for face_landmarks in results.face_landmarks:
        xs = []
        ys = []
        points = []
        for lm in face_landmarks:
            x = int(lm.x * w)
            y = int(lm.y * h)
            x = min(max(x, 0), w - 1)
            y = min(max(y, 0), h - 1)
            points.append((x, y))
            xs.append(x)
            ys.append(y)

        if not xs or not ys:
            continue

        x1, x2 = max(0, min(xs)), min(w - 1, max(xs))
        y1, y2 = max(0, min(ys)), min(h - 1, max(ys))

        def _avg_point(idx_pair):
            p1 = points[idx_pair[0]]
            p2 = points[idx_pair[1]]
            return ((p1[0] + p2[0]) * 0.5, (p1[1] + p2[1]) * 0.5)

        landmarks = {
            "left_eye": _avg_point(left_eye_idx),
            "right_eye": _avg_point(right_eye_idx),
            "nose": points[nose_idx],
            "mouth_left": points[mouth_left_idx],
            "mouth_right": points[mouth_right_idx],
        }

        faces.append({"bbox": (x1, y1, x2, y2), "landmarks": landmarks})

    return faces


def _match_mediapipe_landmarks(face_bbox, mp_faces):
    if not mp_faces:
        return None
    x1, y1, x2, y2 = face_bbox
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5

    for item in mp_faces:
        bx1, by1, bx2, by2 = item["bbox"]
        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
            return item["landmarks"]

    best_item = None
    best_iou = 0.0
    for item in mp_faces:
        iou = _bbox_iou(face_bbox, item["bbox"])
        if iou > best_iou:
            best_iou = iou
            best_item = item

    if best_item and best_iou > 0.02:
        return best_item["landmarks"]

    return None


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


def _compute_raw_quality_metrics(gray):
    gray_f = gray.astype(np.float32, copy=False)
    # Laplacian variance measures edge-energy spread: blur suppresses edges, so variance drops.
    laplacian = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean_intensity = float(np.mean(gray_f))
    contrast = float(np.std(gray_f))
    dark_ratio = float(np.mean(gray_f <= CONFIG.quality_dark_intensity_threshold))
    bright_ratio = float(np.mean(gray_f >= CONFIG.quality_bright_intensity_threshold))
    return {
        "laplacian": laplacian,
        "mean_intensity": mean_intensity,
        "contrast": contrast,
        "dark_ratio": dark_ratio,
        "bright_ratio": bright_ratio,
    }


def _quality_hard_gate_reason(metrics):
    if metrics["laplacian"] < CONFIG.quality_hard_gate_laplacian:
        return (
            f"laplacian {metrics['laplacian']:.1f} < "
            f"{CONFIG.quality_hard_gate_laplacian:.1f}"
        )
    if metrics["contrast"] < CONFIG.quality_hard_gate_contrast:
        return (
            f"contrast {metrics['contrast']:.1f} < "
            f"{CONFIG.quality_hard_gate_contrast:.1f}"
        )
    if metrics["mean_intensity"] < CONFIG.quality_hard_gate_mean_intensity:
        return (
            f"mean_intensity {metrics['mean_intensity']:.1f} < "
            f"{CONFIG.quality_hard_gate_mean_intensity:.1f}"
        )
    return None


def _build_quality_debug_output(final_score, status, metrics, component_scores, reason, extra=None):
    debug_info = {
        "final_score": float(final_score),
        "status": status,
        "metrics": {
            "laplacian": float(metrics.get("laplacian", 0.0)),
            "contrast": float(metrics.get("contrast", 0.0)),
            "mean_intensity": float(metrics.get("mean_intensity", 0.0)),
            "dark_ratio": float(metrics.get("dark_ratio", 0.0)),
            "bright_ratio": float(metrics.get("bright_ratio", 0.0)),
        },
        "component_scores": {
            "sharpness": float(component_scores.get("sharpness", 0.0)),
            "contrast": float(component_scores.get("contrast", 0.0)),
            "exposure": float(component_scores.get("exposure", 0.0)),
            "detection": float(component_scores.get("detection", 0.0)),
            "alignment": float(component_scores.get("alignment", 0.0)),
            "pose": float(component_scores.get("pose", 0.0)),
        },
        "reason": str(reason or ""),
    }
    if extra:
        debug_info.update(extra)
    return debug_info


def _format_factor_values(debug_info):
    factor_values = debug_info.get("factor_values", {})
    if not isinstance(factor_values, dict) or not factor_values:
        return ""

    ordered = ("sharpness", "exposure", "contrast", "detection", "alignment", "pose")
    parts = []
    for name in ordered:
        item = factor_values.get(name)
        if not isinstance(item, dict):
            continue
        score = float(item.get("score", 0.0))
        weighted = float(item.get("weighted", 0.0))
        parts.append(f"{name}={score:.2f} (w={weighted:.2f})")
    return ", ".join(parts)

def _quality_component_factors(debug_info):
    """Return component scores + human-readable details for CLI diagnostics."""
    metrics = debug_info.get("metrics", {})
    component_scores = debug_info.get("component_scores", {})

    return [
        {
            "name": "sharpness",
            "score": component_scores.get("sharpness"),
            "detail": f"laplacian={float(metrics.get('laplacian', 0.0)):.1f}",
        },
        {
            "name": "contrast",
            "score": component_scores.get("contrast"),
            "detail": f"contrast={float(metrics.get('contrast', 0.0)):.1f}",
        },
        {
            "name": "exposure",
            "score": component_scores.get("exposure"),
            "detail": (
                f"mean={float(metrics.get('mean_intensity', 0.0)):.1f}, "
                f"dark={float(metrics.get('dark_ratio', 0.0)):.2f}, "
                f"bright={float(metrics.get('bright_ratio', 0.0)):.2f}"
            ),
        },
        {
            "name": "detection",
            "score": component_scores.get("detection"),
            "detail": f"det={float(debug_info.get('detection_confidence', 0.0)):.2f}",
        },
        {
            "name": "alignment",
            "score": component_scores.get("alignment"),
            "detail": f"source={debug_info.get('alignment_source', 'unknown')}",
        },
        {
            "name": "pose",
            "score": component_scores.get("pose"),
            "detail": "pose consistency",
        },
    ]

def _quality_reason_summary(debug_info, limit=3):
    reason = str(debug_info.get("reason", "")).strip()
    factor_values_text = _format_factor_values(debug_info)
    if reason:
        if factor_values_text:
            return f"{reason}; factors: {factor_values_text}"
        return reason
    factors = [f for f in _quality_component_factors(debug_info) if f.get("score") is not None]
    if not factors:
        return ""
    factors.sort(key=lambda item: float(item.get("score", 0.0)))
    worst = factors[:limit]
    parts = []
    for item in worst:
        parts.append(f"{item['name']}={float(item['score']):.2f} ({item['detail']})")
    summary = "; ".join(parts)
    if factor_values_text:
        summary = f"{summary}; factors: {factor_values_text}"
    return summary


def assess_face_quality(face_crop, detection_confidence=None, landmarks=None):
    """Three-stage face quality scoring: raw metrics, hard gating, normalized weighted scoring."""
    if face_crop is None or face_crop.size == 0:
        metrics = {
            "laplacian": 0.0,
            "contrast": 0.0,
            "mean_intensity": 0.0,
            "dark_ratio": 1.0,
            "bright_ratio": 0.0,
        }
        component_scores = {
            "sharpness": 0.0,
            "contrast": 0.0,
            "exposure": 0.0,
            "detection": 0.0,
            "alignment": 0.0,
            "pose": 0.0,
        }
        debug_info = _build_quality_debug_output(
            final_score=0.0,
            status="Rejected",
            metrics=metrics,
            component_scores=component_scores,
            reason="empty face crop",
            extra={
                "detection_confidence": float(detection_confidence) if detection_confidence is not None else 0.0,
                "alignment_source": "none",
            },
        )
        return 0.0, "Rejected: empty face crop", debug_info

    # Stage 1: RAW metrics from the original crop only (no resize, no CLAHE, no gamma).
    gray = _to_grayscale(face_crop)
    metrics = _compute_raw_quality_metrics(gray)

    # Stage 2: Hard gating protects the embedding model from clearly bad frames.
    rejection_reason = _quality_hard_gate_reason(metrics)

    confidence_value = float(detection_confidence) if detection_confidence is not None else 0.5
    confidence_value = _clamp01(confidence_value)
    detection_score = _score_higher_better(
        confidence_value,
        CONFIG.quality_detection_confidence_low,
        CONFIG.quality_detection_confidence_high,
    )

    # Stage 3: Normalize each metric with linear interpolation and clamp to [0, 1].
    sharpness_score = _score_higher_better(
        metrics["laplacian"],
        CONFIG.quality_sharpness_low,
        CONFIG.quality_sharpness_high,
    )
    contrast_score = _score_higher_better(
        metrics["contrast"],
        CONFIG.quality_contrast_low,
        CONFIG.quality_contrast_high,
    )
    brightness_score = _score_higher_better(
        metrics["mean_intensity"],
        CONFIG.quality_mean_intensity_low,
        CONFIG.quality_mean_intensity_high,
    )
    dark_score = _score_lower_better(
        metrics["dark_ratio"],
        CONFIG.quality_dark_ratio_good,
        CONFIG.quality_dark_ratio_bad,
    )
    bright_score = _score_lower_better(
        metrics["bright_ratio"],
        CONFIG.quality_bright_ratio_good,
        CONFIG.quality_bright_ratio_bad,
    )
    exposure_score = _clamp01(0.50 * brightness_score + 0.25 * dark_score + 0.25 * bright_score)

    edges = cv2.Canny(gray, CONFIG.quality_canny_low, CONFIG.quality_canny_high)
    normalized_landmarks = _normalize_landmarks(landmarks)
    if normalized_landmarks is not None:
        alignment_score, pose_score = _alignment_pose_from_landmarks(normalized_landmarks)
        alignment_source = "landmarks"
    else:
        alignment_score, pose_score = _approximate_alignment_pose(edges)
        alignment_source = "approx"

    global ALIGNMENT_SOURCE_LAST_PRINT_TS, ALIGNMENT_SOURCE_LAST_PRINT_VALUE
    now_ts = time.time()
    # Print source changes immediately, and at most once per second otherwise.
    if (
        alignment_source != ALIGNMENT_SOURCE_LAST_PRINT_VALUE
        or (now_ts - ALIGNMENT_SOURCE_LAST_PRINT_TS) >= 1.0
    ):
        print(f"alignment_source={alignment_source}")
        ALIGNMENT_SOURCE_LAST_PRINT_TS = now_ts
        ALIGNMENT_SOURCE_LAST_PRINT_VALUE = alignment_source

    weighted_sum = (
        CONFIG.quality_weight_sharpness * sharpness_score
        + CONFIG.quality_weight_exposure * exposure_score
        + CONFIG.quality_weight_contrast * contrast_score
        + CONFIG.quality_weight_detection_confidence * detection_score
        + CONFIG.quality_weight_alignment * alignment_score
        + CONFIG.quality_weight_pose * pose_score
    )
    quality_score = _clamp01(weighted_sum)

    component_scores = {
        "sharpness": sharpness_score,
        "contrast": contrast_score,
        "exposure": exposure_score,
        "detection": detection_score,
        "alignment": alignment_score,
        "pose": pose_score,
    }

    factor_values = {
        "sharpness": {
            "raw": float(metrics["laplacian"]),
            "score": float(sharpness_score),
            "weight": float(CONFIG.quality_weight_sharpness),
            "weighted": float(CONFIG.quality_weight_sharpness * sharpness_score),
        },
        "exposure": {
            "raw": {
                "mean_intensity": float(metrics["mean_intensity"]),
                "dark_ratio": float(metrics["dark_ratio"]),
                "bright_ratio": float(metrics["bright_ratio"]),
            },
            "subscores": {
                "brightness": float(brightness_score),
                "dark": float(dark_score),
                "bright": float(bright_score),
            },
            "score": float(exposure_score),
            "weight": float(CONFIG.quality_weight_exposure),
            "weighted": float(CONFIG.quality_weight_exposure * exposure_score),
        },
        "contrast": {
            "raw": float(metrics["contrast"]),
            "score": float(contrast_score),
            "weight": float(CONFIG.quality_weight_contrast),
            "weighted": float(CONFIG.quality_weight_contrast * contrast_score),
        },
        "detection": {
            "raw": float(confidence_value),
            "score": float(detection_score),
            "weight": float(CONFIG.quality_weight_detection_confidence),
            "weighted": float(CONFIG.quality_weight_detection_confidence * detection_score),
        },
        "alignment": {
            "raw": str(alignment_source),
            "score": float(alignment_score),
            "weight": float(CONFIG.quality_weight_alignment),
            "weighted": float(CONFIG.quality_weight_alignment * alignment_score),
        },
        "pose": {
            "raw": float(pose_score),
            "score": float(pose_score),
            "weight": float(CONFIG.quality_weight_pose),
            "weighted": float(CONFIG.quality_weight_pose * pose_score),
        },
    }

    if rejection_reason:
        debug_info = _build_quality_debug_output(
            final_score=0.0,
            status="Rejected",
            metrics=metrics,
            component_scores=component_scores,
            reason=rejection_reason,
            extra={
                "detection_confidence": confidence_value,
                "alignment_source": alignment_source,
                "factor_values": factor_values,
            },
        )
        return 0.0, f"Rejected: {rejection_reason}", debug_info

    if quality_score >= CONFIG.face_quality_good_threshold:
        quality_status = "Good"
        reason = "meets high-quality target"
    elif quality_score >= CONFIG.face_quality_threshold:
        quality_status = "Acceptable"
        reason = "usable but not ideal"
    else:
        quality_status = "Poor"
        reason = "below configured pass threshold"

    debug_info = _build_quality_debug_output(
        final_score=quality_score,
        status=quality_status,
        metrics=metrics,
        component_scores=component_scores,
        reason=reason,
        extra={
            "detection_confidence": confidence_value,
            "alignment_source": alignment_source,
            "factor_values": factor_values,
        },
    )
    return quality_score, quality_status, debug_info


# -------------------------------
# Improved embedding extraction - Two-Factor Verification
# -------------------------------
def extract_embedding_ensemble(face_crop):
    """Extract embeddings using BOTH DeepFace models for Two-Factor Verification"""
    try:
        if DeepFace is None:
            raise RuntimeError("Runtime not initialized. Call initialize_runtime() before recognition.")

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
    
    def find_best_match(self, query_embeddings, user_embeddings_list, user_info, face_quality, _allow_retry=True):
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

        primary_query = _normalize_embedding_vector(primary_emb)
        secondary_query = _normalize_embedding_vector(secondary_emb)
        if primary_query is None or secondary_query is None:
            print("  Warning: Invalid query embedding(s) for FAISS search")
            return None, []

        index_primary = STATE.faiss_indexes.get(CONFIG.primary_model)
        index_secondary = STATE.faiss_indexes.get(CONFIG.secondary_model)
        id_map_primary = STATE.faiss_id_maps.get(CONFIG.primary_model, {})
        id_map_secondary = STATE.faiss_id_maps.get(CONFIG.secondary_model, {})

        if index_primary is None or index_secondary is None:
            print("  Warning: FAISS indexes are not ready for both models")
            return None, []

        if index_primary.ntotal == 0 or index_secondary.ntotal == 0:
            return None, []

        if primary_query.shape[0] != index_primary.d or secondary_query.shape[0] != index_secondary.d:
            print("  Warning: Query embedding dimension mismatch with FAISS index")
            return None, []

        top_k_primary = min(5, index_primary.ntotal)
        top_k_secondary = min(5, index_secondary.ntotal)

        D_primary, I_primary = index_primary.search(primary_query.reshape(1, -1).astype(np.float32), top_k_primary)
        D_secondary, I_secondary = index_secondary.search(secondary_query.reshape(1, -1).astype(np.float32), top_k_secondary)

        candidate_scores = {}
        map_mismatch = False

        for score, idx in zip(D_primary[0], I_primary[0]):
            if idx < 0:
                continue
            faiss_id = int(idx)
            mapping = id_map_primary.get(faiss_id)
            if mapping is None:
                map_mismatch = True
                break

            user_id, _embedding_id = mapping
            if user_id not in candidate_scores:
                candidate_scores[user_id] = {
                    CONFIG.primary_model: None,
                    CONFIG.secondary_model: None,
                }
            current = candidate_scores[user_id].get(CONFIG.primary_model)
            score = float(score)
            if current is None or score > current:
                candidate_scores[user_id][CONFIG.primary_model] = score

        if not map_mismatch:
            for score, idx in zip(D_secondary[0], I_secondary[0]):
                if idx < 0:
                    continue
                faiss_id = int(idx)
                mapping = id_map_secondary.get(faiss_id)
                if mapping is None:
                    map_mismatch = True
                    break

                user_id, _embedding_id = mapping
                if user_id not in candidate_scores:
                    candidate_scores[user_id] = {
                        CONFIG.primary_model: None,
                        CONFIG.secondary_model: None,
                    }
                current = candidate_scores[user_id].get(CONFIG.secondary_model)
                score = float(score)
                if current is None or score > current:
                    candidate_scores[user_id][CONFIG.secondary_model] = score

        if map_mismatch:
            if _allow_retry:
                _faiss_rebuild_with_reason("search returned vector IDs missing from id map")
                return self.find_best_match(query_embeddings, user_embeddings_list, user_info, face_quality, _allow_retry=False)
            return None, []

        user_lookup = {
            info.get('id'): (idx, info)
            for idx, info in enumerate(user_info)
            if isinstance(info, dict) and info.get('id') is not None
        }

        best_match = None
        best_user_idx = -1
        best_avg_confidence = -1.0

        # Candidates are pre-filtered by FAISS top-K from both models.
        for user_id, scores in candidate_scores.items():
            user_snapshot = user_lookup.get(user_id)
            if user_snapshot is None:
                continue

            user_idx, info = user_snapshot
            primary_confidence = scores.get(CONFIG.primary_model)
            secondary_confidence = scores.get(CONFIG.secondary_model)
            if primary_confidence is None or secondary_confidence is None:
                continue

            primary_best_dist = 1.0 - float(primary_confidence)
            secondary_best_dist = 1.0 - float(secondary_confidence)
            
            primary_pass = primary_confidence >= CONFIG.primary_threshold
            secondary_pass = secondary_confidence >= CONFIG.secondary_threshold
            
            if primary_pass and secondary_pass:
                # Both models confirmed - use average confidence
                avg_confidence = (primary_confidence + secondary_confidence) / 2
                avg_distance = (primary_best_dist + secondary_best_dist) / 2
                
                if avg_confidence > best_avg_confidence:
                    best_avg_confidence = avg_confidence
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
                        'user_info': info
                    }
                    
                    print(f"  ✓ 2-Factor Verified: {info['name']}")
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

def _get_track_motion_px(face_id):
    """Estimate per-track motion using the latest two center points."""
    tracker = STATE.face_stability_tracker.get(face_id) if face_id is not None else None
    if not tracker:
        return 0.0
    positions = tracker.get("positions", [])
    if len(positions) < 2:
        return 0.0
    (x1, y1), (x2, y2) = positions[-2], positions[-1]
    return float(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)


def embedding_similarity_check(new_embedding, existing_embeddings, threshold=0.98):
    """Return True when incoming embedding is effectively a near-duplicate."""
    incoming = _normalize_embedding_vector(new_embedding)
    if incoming is None:
        return False

    for existing in existing_embeddings or []:
        existing_vec = _normalize_embedding_vector(existing)
        if existing_vec is None or existing_vec.shape[0] != incoming.shape[0]:
            continue
        similarity = float(np.dot(incoming, existing_vec))
        if similarity >= float(threshold):
            return True
    return False


def _ensure_embedding_metadata_shape(metadata_by_model, embeddings_by_model):
    """Keep per-model metadata aligned with embedding counts for safe pruning/scoring."""
    shaped = {}
    for model_name in CONFIG.models:
        emb_list = normalize_embeddings_by_model(embeddings_by_model).get(model_name, [])
        incoming_meta = metadata_by_model.get(model_name, []) if isinstance(metadata_by_model, dict) else []
        if not isinstance(incoming_meta, list):
            incoming_meta = []

        aligned = []
        now = time.time()
        for idx in range(len(emb_list)):
            if idx < len(incoming_meta) and isinstance(incoming_meta[idx], dict):
                meta = dict(incoming_meta[idx])
            else:
                meta = {}
            meta.setdefault("quality", 0.0)
            meta.setdefault("confidence", 0.0)
            meta.setdefault("timestamp", now)
            meta["weight"] = float(meta.get("confidence", 0.0)) * float(meta.get("quality", 0.0))
            aligned.append(meta)
        shaped[model_name] = aligned
    return shaped


def batch_consistency_check(embeddings_batch_by_model):
    """Validate that a candidate batch is identity-consistent via centroid spread."""
    normalized = normalize_embeddings_by_model(embeddings_batch_by_model)
    spread_by_model = {}
    total_samples = 0

    for model_name in CONFIG.models:
        emb_list = normalized.get(model_name, [])
        if len(emb_list) < CONFIG.candidate_batch_min_size:
            return False, {
                "reason": f"insufficient batch size for {model_name}",
                "spread": spread_by_model,
            }

        vectors = []
        for emb in emb_list:
            vec = _normalize_embedding_vector(emb)
            if vec is None:
                continue
            vectors.append(vec)

        if len(vectors) < CONFIG.candidate_batch_min_size:
            return False, {
                "reason": f"insufficient valid vectors for {model_name}",
                "spread": spread_by_model,
            }

        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        centroid = matrix.mean(axis=0)
        centroid_norm = np.linalg.norm(centroid)
        if centroid_norm < 1e-8:
            return False, {
                "reason": f"degenerate centroid for {model_name}",
                "spread": spread_by_model,
            }
        centroid = centroid / centroid_norm

        similarities = np.clip(matrix @ centroid, -1.0, 1.0)
        spread = float(max(0.0, 1.0 - float(np.mean(similarities))))
        spread_by_model[model_name] = spread
        total_samples += len(vectors)

        if spread > CONFIG.candidate_max_spread:
            return False, {
                "reason": f"inconsistent batch for {model_name}",
                "spread": spread_by_model,
            }

    return total_samples >= (CONFIG.candidate_batch_min_size * len(CONFIG.models)), {
        "reason": "ok",
        "spread": spread_by_model,
    }


def update_user_embeddings(user_id, new_embeddings_with_meta):
    """Safely merge validated embeddings, enforce per-user limits, and persist metadata."""
    user_idx = _get_user_index_by_id(user_id)
    if user_idx == -1:
        print(f"Rejected embedding: user {user_id} not found")
        return False

    conn = sqlite3.connect(CONFIG.db_path)
    c = conn.cursor()
    c.execute("SELECT embeddings, image_paths, embedding_metadata FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        print(f"Rejected embedding: user {user_id} missing in DB")
        return False

    existing_emb_blob, existing_paths_str, existing_meta_blob = row
    existing_embeddings = normalize_embeddings_by_model(pickle.loads(existing_emb_blob) if existing_emb_blob else {})
    existing_meta = pickle.loads(existing_meta_blob) if existing_meta_blob else {}
    existing_meta = _ensure_embedding_metadata_shape(existing_meta, existing_embeddings)

    incoming = {}
    incoming_meta = {}
    for model_name in CONFIG.models:
        model_items = new_embeddings_with_meta.get(model_name, []) if isinstance(new_embeddings_with_meta, dict) else []
        model_vectors = []
        model_meta = []

        for item in model_items:
            if not isinstance(item, dict):
                continue
            vec = _normalize_embedding_vector(item.get("embedding"))
            if vec is None:
                continue
            model_vectors.append(vec)
            meta = {
                "quality": float(item.get("quality", 0.0)),
                "confidence": float(item.get("confidence", 0.0)),
                "timestamp": float(item.get("timestamp", time.time())),
            }
            meta["weight"] = meta["quality"] * meta["confidence"]
            model_meta.append(meta)

        incoming[model_name] = model_vectors
        incoming_meta[model_name] = model_meta

    for model_name in CONFIG.models:
        current_vectors = list(existing_embeddings.get(model_name, []))
        current_meta = list(existing_meta.get(model_name, []))

        for idx, vec in enumerate(incoming.get(model_name, [])):
            if embedding_similarity_check(
                vec,
                current_vectors,
                threshold=CONFIG.candidate_duplicate_similarity,
            ):
                print(f"Rejected embedding: duplicate for user {user_id} model {model_name}")
                continue
            current_vectors.append(vec)
            current_meta.append(incoming_meta.get(model_name, [])[idx])

        max_count = int(CONFIG.max_embeddings_per_user_per_model)
        if len(current_vectors) > max_count:
            # Keep strongest + newest mix by ranking with weight first, then timestamp.
            ranked_indices = sorted(
                range(len(current_vectors)),
                key=lambda i: (
                    float(current_meta[i].get("weight", 0.0)),
                    float(current_meta[i].get("timestamp", 0.0)),
                ),
                reverse=True,
            )
            keep_indices = sorted(ranked_indices[:max_count])
            current_vectors = [current_vectors[i] for i in keep_indices]
            current_meta = [current_meta[i] for i in keep_indices]

        existing_embeddings[model_name] = current_vectors
        existing_meta[model_name] = current_meta

    updated_emb_blob = pickle.dumps(existing_embeddings)
    updated_meta_blob = pickle.dumps(existing_meta)
    c.execute(
        """
        UPDATE users
        SET embeddings = ?, embedding_metadata = ?, last_updated = CURRENT_TIMESTAMP
        WHERE user_id = ?
        """,
        (updated_emb_blob, updated_meta_blob, user_id),
    )
    conn.commit()
    conn.close()

    STATE.all_user_embeddings[user_idx] = existing_embeddings
    refresh_faiss()
    return True


def add_to_candidate_buffer(
    user_id,
    embedding,
    confidence,
    quality,
    face_id=None,
    identity_locked=False,
    quality_debug=None,
):
    """Stage high-quality embeddings after strict gating and temporal checks."""
    confidence = float(confidence)
    quality = float(quality)

    if confidence < CONFIG.candidate_min_confidence:
        print("Rejected embedding: low confidence")
        return False
    if quality < CONFIG.face_quality_good_threshold:
        print("Rejected embedding: low quality")
        if quality_debug:
            reason = _quality_reason_summary(quality_debug)
            if reason:
                print(f"  Not 'Good' yet because: {reason}")
        return False
    if not identity_locked:
        print("Rejected embedding: identity not locked")
        return False

    normalized = normalize_embeddings_by_model(embedding)
    if any(model_name not in normalized or not normalized[model_name] for model_name in CONFIG.models):
        print("Rejected embedding: missing model embeddings")
        return False

    consistency_key = face_id if face_id is not None else f"uid:{user_id}"
    record = STATE.embedding_consistency_tracker.get(
        consistency_key,
        {"user_id": user_id, "count": 0},
    )
    if record.get("user_id") == user_id:
        record["count"] = int(record.get("count", 0)) + 1
    else:
        record = {"user_id": user_id, "count": 1}
    STATE.embedding_consistency_tracker[consistency_key] = record

    if record["count"] < int(CONFIG.candidate_consistent_frames):
        print("Rejected embedding: insufficient temporal consistency")
        return False

    now = time.time()
    user_candidates = STATE.embedding_candidates.setdefault(user_id, [])
    if user_candidates:
        last_ts = float(user_candidates[-1].get("timestamp", 0.0))
        if (now - last_ts) < CONFIG.candidate_min_time_gap_seconds:
            print("Rejected embedding: duplicate timestamp window")
            return False

    motion_px = _get_track_motion_px(face_id)
    if motion_px < CONFIG.candidate_static_motion_threshold_px:
        print("Rejected embedding: static face sequence")
        return False

    primary_vec = _normalize_embedding_vector(normalized.get(CONFIG.primary_model, [None])[0])
    user_candidates.append(
        {
            "embedding": primary_vec,
            "embeddings_by_model": {
                model_name: _normalize_embedding_vector(normalized.get(model_name, [None])[0])
                for model_name in CONFIG.models
            },
            "quality": quality,
            "confidence": confidence,
            "timestamp": now,
        }
    )
    return True


def process_embedding_candidates():
    """Validate candidate batches and commit only consistent, bounded updates."""
    if not STATE.embedding_candidates:
        return 0

    accepted_users = 0
    for user_id in list(STATE.embedding_candidates.keys()):
        candidates = STATE.embedding_candidates.get(user_id, [])
        if len(candidates) < CONFIG.candidate_batch_min_size:
            continue

        batch = candidates[:CONFIG.candidate_batch_min_size]
        batch_embeddings = {model_name: [] for model_name in CONFIG.models}
        for candidate in batch:
            model_map = candidate.get("embeddings_by_model", {})
            for model_name in CONFIG.models:
                vec = _normalize_embedding_vector(model_map.get(model_name))
                if vec is not None:
                    batch_embeddings[model_name].append(vec)

        is_consistent, diagnostics = batch_consistency_check(batch_embeddings)
        if not is_consistent:
            print(f"Rejected embedding: {diagnostics.get('reason', 'inconsistent batch')}")
            STATE.embedding_candidates[user_id] = candidates[1:]
            continue

        payload = {model_name: [] for model_name in CONFIG.models}
        for candidate in batch:
            for model_name in CONFIG.models:
                vec = _normalize_embedding_vector(candidate.get("embeddings_by_model", {}).get(model_name))
                if vec is None:
                    continue
                payload[model_name].append(
                    {
                        "embedding": vec,
                        "quality": float(candidate.get("quality", 0.0)),
                        "confidence": float(candidate.get("confidence", 0.0)),
                        "timestamp": float(candidate.get("timestamp", time.time())),
                    }
                )

        if update_user_embeddings(user_id, payload):
            accepted_users += 1
            STATE.embedding_candidates[user_id] = candidates[CONFIG.candidate_batch_min_size:]
            print(f"Accepted {CONFIG.candidate_batch_min_size} embeddings for user {user_id}")
        else:
            print(f"Rejected embedding: failed to persist user {user_id}")
            STATE.embedding_candidates[user_id] = candidates[1:]

        if not STATE.embedding_candidates.get(user_id):
            STATE.embedding_candidates.pop(user_id, None)

    return accepted_users

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
        if quality_debug:
            reason = _quality_reason_summary(quality_debug)
            if reason:
                print(f"  Low quality factors: {reason}")
        return None

    metrics = quality_debug.get("metrics", {}) if quality_debug else {}
    det_conf = float(quality_debug.get("detection_confidence", 0.0)) if quality_debug else 0.0
    
    print(
        f"  Face quality: {quality_score:.2f} ({quality_status}) "
        f"| lap={float(metrics.get('laplacian', 0.0)):.1f} "
        f"| det={det_conf:.2f}"
    )
    factor_values_text = _format_factor_values(quality_debug) if quality_debug else ""
    if factor_values_text:
        print(f"  Factor values: {factor_values_text}")
    if quality_status != "Good" and quality_debug:
        reason = _quality_reason_summary(quality_debug)
        if reason:
            print(f"  Not 'Good' yet because: {reason}")

    # Quality is computed on raw pixels; enhancement is applied only after passing quality gates.
    face_for_embedding = _preprocess_face_for_embedding(face_crop)
    embeddings = extract_embedding_ensemble(face_for_embedding)
    if not embeddings:
        print("  Failed to extract embeddings")
        return None
    
    best_match, all_distances = face_recognition_system.find_best_match(
        embeddings, STATE.all_user_embeddings, STATE.user_info, quality_score
    )
    
    if best_match:
        user_id = best_match['user_info']['id']
        confidence_value = float(best_match.get('confidence', 0.0))

        track_state = STATE.tracked_identities.get(face_id) if face_id is not None else None
        identity_locked = bool(track_state.get("identity_locked")) if track_state else False
        added = add_to_candidate_buffer(
            user_id=user_id,
            embedding=embeddings,
            confidence=confidence_value,
            quality=quality_score,
            face_id=face_id,
            identity_locked=identity_locked,
            quality_debug=quality_debug,
        )
        if added:
            pending_count = len(STATE.embedding_candidates.get(user_id, []))
            print(f"Candidate embedding buffered for user {user_id} (pending={pending_count})")

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
            "identity_locked": False,
            "user": None,
            "last_seen": current_time,
            "last_recognition_time": 0.0,
            "confidence": 0.0,
            "quality": 0.0,
        }
    else:
        STATE.tracked_identities[track_id]["last_seen"] = current_time
    return STATE.tracked_identities[track_id]

def parse_confidence_value(confidence_text):
    """Parse confidence text like '87.50%' into a 0..1 float."""
    if confidence_text is None:
        return 0.0
    if isinstance(confidence_text, (int, float)):
        value = float(confidence_text)
        return value if value <= 1.0 else value / 100.0

    try:
        text = str(confidence_text).strip().replace('%', '')
        value = float(text)
        return value / 100.0 if value > 1.0 else value
    except (ValueError, TypeError):
        return 0.0

def update_track_identity_state(track_state, result, current_time, quality_score):
    """Apply recognition result to a per-track identity state machine."""
    track_state["last_seen"] = current_time
    track_state["last_recognition_time"] = current_time
    track_state["quality"] = float(quality_score)

    if result is True:
        user_snapshot = dict(STATE.recognized_user) if STATE.recognized_user else None
        confidence_value = parse_confidence_value(user_snapshot.get("confidence") if user_snapshot else 0.0)

        track_state["recognized"] = True
        track_state["user"] = user_snapshot
        track_state["confidence"] = confidence_value
        # Lock identity only when confidence and quality are both strong.
        track_state["identity_locked"] = (
            confidence_value >= CONFIG.identity_lock_confidence_threshold
            and float(quality_score) >= CONFIG.face_quality_good_threshold
        )
    elif result is False:
        track_state["recognized"] = False
        track_state["identity_locked"] = False
        track_state["user"] = None
        track_state["confidence"] = 0.0

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
        STATE.embedding_consistency_tracker.pop(track_id, None)
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
    if not APP_INITIALIZED:
        initialize_runtime()

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

        mp_faces = _extract_mediapipe_faces(frame) if CONFIG.use_mediapipe_landmarks else []

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
                if face_crop.size == 0:
                    continue

                mp_landmarks = _match_mediapipe_landmarks((x1, y1, x2, y2), mp_faces)
                quality_score, quality_status, quality_debug = assess_face_quality(
                    face_crop,
                    detection_confidence=detection_confidence,
                    landmarks=mp_landmarks,
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
                    confidence_value = track_state.get("confidence", 0.0)
                    if track_state.get("identity_locked"):
                        identity_text = f"{cached_user['name']} LOCKED {confidence_value:.0%}"
                        identity_color = (0, 255, 0)
                    else:
                        identity_text = f"{cached_user['name']} UNLOCKED {confidence_value:.0%}"
                        identity_color = (0, 255, 255)
                elif track_state and track_state.get("last_recognition_time", 0.0) > 0.0:
                    identity_text = "UNKNOWN"
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
            quality_score = quality_tuple[0]

            if STATE.manual_registration_requested and not STATE.manual_registration_active and track_state.get("recognized"):
                print("Face already in database. Manual registration canceled.")
                STATE.manual_registration_requested = False
                continue

            # Per-track state machine:
            # - UNLOCKED tracks can run recognition after cooldown.
            # - LOCKED tracks only revalidate every revalidation interval.
            last_attempt = track_state.get("last_recognition_time", 0.0)
            should_recognize = False
            is_revalidation = False

            if track_state.get("identity_locked"):
                if (current_time - last_attempt) >= CONFIG.revalidation_interval_seconds:
                    should_recognize = True
                    is_revalidation = True
            else:
                if (current_time - last_attempt) >= CONFIG.recognition_cooldown_seconds:
                    should_recognize = True

            if not should_recognize:
                continue

            # Mark attempt time now so failed/unknown attempts still obey cooldown.
            track_state["last_recognition_time"] = current_time
            track_state["last_seen"] = current_time
            track_state["quality"] = float(quality_score)
            
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
                    update_track_identity_state(track_state, True, current_time, quality_score)
                    print("Face already in database. Manual registration canceled.")
                    STATE.manual_registration_requested = False
                else:
                    update_track_identity_state(track_state, False, current_time, quality_score)
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
                update_track_identity_state(track_state, True, current_time, quality_score)
            elif result is False:
                # Revalidation failure explicitly unlocks and marks unknown.
                update_track_identity_state(track_state, False, current_time, quality_score)
                if is_revalidation:
                    print(f"Track {face_id}: identity unlock after failed revalidation")

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

        if (current_time - float(STATE.last_embedding_commit_time)) >= CONFIG.embedding_commit_interval_seconds:
            accepted_batches = process_embedding_candidates()
            if accepted_batches > 0:
                print(f"Background commit: {accepted_batches} user batch(es) committed")
            STATE.last_embedding_commit_time = current_time

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
            STATE.embedding_candidates.clear()
            STATE.embedding_consistency_tracker.clear()
            STATE.last_embedding_commit_time = 0.0
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
    conn = db_connect(CONFIG.db_path)
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
        all_embeddings = normalize_embeddings_by_model(all_embeddings)
        user_id = save_user_with_multiple_embeddings(all_embeddings, image_paths, name, sr_code, course)
        
        STATE.all_user_embeddings.append(all_embeddings)
        STATE.user_info.append({
            'id': user_id,
            'name': name,
            'sr_code': sr_code
        })
        STATE.user_count = len(STATE.user_info)

        add_embeddings_incremental(
            user_id=user_id,
            embeddings_by_model=all_embeddings,
            start_offsets={model_name: 0 for model_name in CONFIG.models},
        )
        
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
            refresh_faiss()
            
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
                    
                conn = db_connect(CONFIG.db_path)
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

                    removed = remove_user_incremental(user_id)
                    user_idx = _get_user_index_by_id(user_id)
                    if user_idx != -1:
                        STATE.all_user_embeddings.pop(user_idx)
                        STATE.user_info.pop(user_idx)
                    STATE.user_count = len(STATE.user_info)

                    if not removed:
                        refresh_faiss()
                    
                conn.close()
            except ValueError:
                print("Invalid input")
                
        elif choice == '5':
            conn = db_connect(CONFIG.db_path)
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
                conn = db_connect(CONFIG.db_path)
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
                refresh_faiss()
                print("Database reset complete")
            else:
                print("Reset cancelled")
                
        elif choice == '7':
            print("\nExiting... Goodbye!")
            break
        else:
            print("Invalid choice")


def parse_args():
    parser = argparse.ArgumentParser(description="Face Recognition Kiosk")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run the Flask web interface alongside the interactive CLI menu.",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Run only the Flask web interface.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        help="Host interface for web mode.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASK_RUN_PORT", 5000)),
        help="Port for web mode.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode in web mode.",
    )
    return parser.parse_args()


def start_web_server(host, port, debug):
    """Run the Flask site in a background thread when CLI mode is also active."""
    app = create_app()
    server_thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": host,
            "port": port,
            "debug": debug,
            "use_reloader": False,
        },
        daemon=True,
        name="flask-web-server",
    )
    server_thread.start()
    return server_thread


# -------------------------------
# Run the system
# -------------------------------
if __name__ == "__main__":
    initialize_runtime()
    log_header("CCTV Face Recognition System - Initialization")
    log_step(f"Database: {CONFIG.db_path}")
    log_step(f"Face models: {CONFIG.primary_model} + {CONFIG.secondary_model}")
    log_step(f"Base threshold: {CONFIG.base_threshold}")
    log_step(f"Users in database: {STATE.user_count}")

    if args.web_only:
        log_step(f"Starting web server at http://{args.host}:{args.port}")
        create_app().run(host=args.host, port=args.port, debug=args.debug)
    else:
        if args.web:
            log_step(f"Starting web server at http://{args.host}:{args.port}")
            start_web_server(args.host, args.port, args.debug)
        main_menu()

