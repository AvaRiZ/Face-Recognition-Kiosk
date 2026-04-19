import cv2
import numpy as np
import sqlite3
import os
import time
import pickle
from collections import deque
import statistics
import torch
import tensorflow as tf
from ultralytics import YOLO
from deepface import DeepFace

def configure_gpu(target_index=1):
    """Configure CUDA device for both Torch/Ultralytics and TensorFlow/DeepFace."""
    # Torch / Ultralytics
    if torch.cuda.is_available():
        if torch.cuda.device_count() > target_index:
            torch.cuda.set_device(target_index)
        else:
            torch.cuda.set_device(0)

    # TensorFlow / DeepFace
    try:
        gpus = tf.config.list_physical_devices("GPU")
        if gpus:
            if len(gpus) > target_index:
                tf.config.set_visible_devices(gpus[target_index], "GPU")
                tf.config.experimental.set_memory_growth(gpus[target_index], True)
            else:
                tf.config.set_visible_devices(gpus[0], "GPU")
                tf.config.experimental.set_memory_growth(gpus[0], True)
    except Exception as e:
        print(f"GPU configuration warning (TensorFlow): {e}")

configure_gpu(target_index=1)

def log_gpu_info():
    """Log GPU name for Torch and TensorFlow."""
    # Torch
    if torch.cuda.is_available():
        try:
            torch_name = torch.cuda.get_device_name(0)
            print(f"GPU (Torch): {torch_name}")
        except Exception as e:
            print(f"GPU (Torch) info unavailable: {e}")
    else:
        print("GPU (Torch): CUDA not available")

    # TensorFlow
    try:
        tf_gpus = tf.config.list_logical_devices("GPU")
        if tf_gpus:
            print(f"GPU (TensorFlow): {tf_gpus[0].name}")
        else:
            print("GPU (TensorFlow): No GPU visible")
            print("WARNING: TensorFlow cannot see a GPU. DeepFace will run on CPU.")
    except Exception as e:
        print(f"GPU (TensorFlow) info unavailable: {e}")
        print("WARNING: TensorFlow GPU check failed. DeepFace may run on CPU.")

log_gpu_info()

# -------------------------------
# Configuration - Aligned with app.py
# -------------------------------
MODEL_PATH = "models/face_yolov8m.pt"
DB_PATH = "database/faces_improved.db"
BASE_SAVE_DIR = "faces_improved"

# Dual Models (same as app.py)
PRIMARY_MODEL = "ArcFace"
SECONDARY_MODEL = "Facenet"
MODELS = [PRIMARY_MODEL, SECONDARY_MODEL]

print("Loading YOLOv8 model...")
DEVICE = "cpu"
if torch.cuda.is_available():
    if torch.cuda.device_count() > 1:
        DEVICE = "cuda:1"
    else:
        DEVICE = "cuda:0"

yolo_model = YOLO(MODEL_PATH)
try:
    yolo_model.to(DEVICE)
except Exception as e:
    print(f"GPU warning (YOLO): {e}")
print("✓ YOLOv8 loaded!")
print(f"Using Dual Models: {PRIMARY_MODEL} + {SECONDARY_MODEL}")

# -------------------------------
# Database Functions (Aligned with app.py schema)
# -------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()
    print("Database initialized (app.py compatible schema)")

def save_user_with_multiple_embeddings(embeddings_by_model, image_paths, name, sr_code, program):
    conn = sqlite3.connect(DB_PATH)
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
        """, (name, program, embeddings_blob, ';'.join(image_paths), infer_embedding_dim(all_embeddings), user_id))
    else:
        embeddings_by_model = normalize_embeddings_by_model(embeddings_by_model)
        embeddings_blob = pickle.dumps(embeddings_by_model)
        embedding_dim = infer_embedding_dim(embeddings_by_model)
        c.execute("""
            INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, sr_code, program, embeddings_blob, ';'.join(image_paths), embedding_dim))
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
    normalized = {}
    for model_name, value in (embeddings_by_model or {}).items():
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

def load_all_embeddings():
    """Load all embeddings for compatibility with app.py"""
    conn = sqlite3.connect(DB_PATH)
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
    
    print(f"Loaded {len(all_embeddings)} users with embeddings")
    return all_embeddings, user_info

# -------------------------------
# Face quality (from app.py)
# -------------------------------
FACE_QUALITY_THRESHOLD = 0.2
MIN_FACE_SIZE = 60

def assess_face_quality(face_crop):
    if face_crop is None or face_crop.size == 0:
        return 0.0, "No face"
    
    h, w = face_crop.shape[:2]
    size_score = min((h * w) / (MIN_FACE_SIZE * MIN_FACE_SIZE), 1.0)
    
    if len(face_crop.shape) == 3:
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = face_crop
    
    brightness = np.mean(gray)
    brightness_score = 1.0 - min(abs(brightness - 140) / 100, 1.0)
    
    contrast = np.std(gray)
    contrast_score = min(contrast / 60, 1.0)
    
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = laplacian.var()
    sharpness_score = min(sharpness / 1000, 1.0)
    
    aspect_ratio = w / h
    aspect_score = 1.0 - min(abs(aspect_ratio - 0.85) / 0.3, 1.0)
    
    symmetry_score = 0.5  # Simplified
    
    quality_score = 0.20 * size_score + 0.15 * brightness_score + 0.15 * contrast_score + 0.20 * sharpness_score + 0.15 * aspect_score + 0.15 * symmetry_score
    quality_status = "Good" if quality_score > FACE_QUALITY_THRESHOLD else "Poor"
    
    return quality_score, quality_status

# -------------------------------
# Dual Embedding Extraction (from app.py)
# -------------------------------
def extract_embedding_ensemble(face_crop):
    try:
        if len(face_crop.shape) == 3 and face_crop.shape[2] == 3:
            face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        else:
            if len(face_crop.shape) == 2:
                face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_GRAY2RGB)
            else:
                face_rgb = face_crop
        
        embeddings = {}
        
        for model_name in MODELS:
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
                if norm > 1e-8:
                    embedding = embedding / norm
                
                embeddings[model_name] = [embedding]
                print(f"  ✓ {model_name} embedding extracted (dim: {len(embedding)})")
                
            except Exception as e:
                print(f"  ✗ {model_name} failed: {e}")
        
        return embeddings
        
    except Exception as e:
        print(f"Embedding extraction error: {e}")
        return []

# -------------------------------
# Face Capture (Enhanced)
# -------------------------------
def capture_best_face():
    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("Error: Cannot open webcam")
        return None
    
    print("\n" + "="*50)
    print("FACE REGISTRATION - Dual Model Capture")
    print("="*50)
    print("Press 's' to save when ready, 'q' to quit")
    
    best_face = None
    best_confidence = 0
    best_quality = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame = cv2.resize(frame, (640, 480))
        results = yolo_model(frame, conf=0.5, device=DEVICE)
        
        current_best_face = None
        current_best_conf = 0
        current_best_quality = 0
        
        for r in results:
            for box in r.boxes:
                conf = float(box.conf[0])
                if conf > current_best_conf:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    if (x2-x1) < MIN_FACE_SIZE or (y2-y1) < MIN_FACE_SIZE:
                        continue
                    
                    face_crop = frame[y1:y2, x1:x2]
                    quality_score, _ = assess_face_quality(face_crop)
                    combined_score = conf * quality_score
                    
                    if combined_score > current_best_conf:
                        current_best_face = face_crop
                        current_best_conf = conf
                        current_best_quality = quality_score
        
        if current_best_face is not None and current_best_conf > best_confidence:
            best_face = current_best_face
            best_confidence = current_best_conf
            best_quality = current_best_quality
        
        # Draw
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                color = (0, 255, 0) if conf == best_confidence else (0, 165, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"Conf: {conf:.1f}", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        
        cv2.putText(frame, f"Best Conf: {best_confidence:.2f} Q: {best_quality:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
        cv2.putText(frame, "'s' save 'q' quit", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
        
        if best_face is not None:
            preview = cv2.resize(best_face, (120, 120))
            frame[10:130, 450:570] = preview
        
        cv2.imshow('Register - Dual Model', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            if best_confidence > 0.6 and best_quality > 0.2:
                print(f"✓ Captured! Conf: {best_confidence:.2f} Quality: {best_quality:.2f}")
                break
            else:
                print("Better face needed!")
        elif key == ord('q'):
            best_face = None
            break
    
    cap.release()
    cv2.destroyAllWindows()
    return best_face

# -------------------------------
# Main Registration
# -------------------------------
def register_new_user():
    init_db()
    
    # Load DB
    all_user_embeddings, user_info = load_all_embeddings()
    
    # Capture
    print("\nCapturing face...")
    face_crop = capture_best_face()
    if face_crop is None:
        return
    
    # Extract dual embeddings
    print("\nExtracting dual embeddings...")
    embeddings = extract_embedding_ensemble(face_crop)
    if not embeddings:
        print("Failed to extract embeddings")
        return
    
    print(f"✓ Dual embeddings: ArcFace({len(embeddings[PRIMARY_MODEL])}dim), Facenet({len(embeddings[SECONDARY_MODEL])}dim)")
    
    # Input info
    print("\n=== USER INFO ===")
    name = input("Full name: ").strip()
    sr_code = input("SR Code: ").strip()
    program = input("Program: ").strip()
    
    if not all([name, sr_code, program]):
        print("All fields required!")
        return
    
    # SR check
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE sr_code = ?", (sr_code,))
    if c.fetchone():
        if input(f"SR {sr_code} exists. Override? (y/n): ").lower() != 'y':
            return
    conn.close()
    
    # Save images/embeddings
    os.makedirs(BASE_SAVE_DIR, exist_ok=True)
    timestamp = int(time.time() * 1000)
    sr_folder = os.path.join(BASE_SAVE_DIR, sr_code)
    os.makedirs(sr_folder, exist_ok=True)
    filename = os.path.join(sr_folder, f"face_{timestamp}.jpg")
    cv2.imwrite(filename, face_crop)
    
    # Save to DB (app.py compatible)
    user_id = save_user_with_multiple_embeddings(embeddings, [filename], name, sr_code, program)
    
    print(f"\n✅ REGISTERED! ID: {user_id}")
    print(f"Name: {name} | SR: {sr_code} | Dual embeddings saved")
    
    cv2.imshow('Success', face_crop)
    cv2.waitKey(2000)
    cv2.destroyAllWindows()

# -------------------------------
# Menu
# -------------------------------
def main_menu():
    while True:
        print("\n=== DUAL MODEL REGISTRATION (app.py Compatible) ===")
        print("1. Register New User")
        print("2. List Users")
        print("3. Exit")
        
        choice = input("\nChoice: ").strip()
        
        if choice == '1':
            register_new_user()
        elif choice == '2':
            _, user_info = load_all_embeddings()
            print("\nUsers:")
            for info in user_info:
                print(f"ID {info['id']}: {info['name']} ({info['sr_code']})")
        elif choice == '3':
            break

if __name__ == "__main__":
    main_menu()
