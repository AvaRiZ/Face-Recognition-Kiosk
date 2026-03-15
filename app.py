import cv2
import numpy as np
import sqlite3
import os
import time
from ultralytics import YOLO
from deepface import DeepFace
import pickle
from collections import deque
import statistics

# -------------------------------
# Configuration - Two-Factor Verification
# -------------------------------
MODEL_PATH = "models/face_yolov8m.pt"
DB_PATH = "database/faces_improved.db"
BASE_SAVE_DIR = "faces_improved"

# Two-Factor Models: BOTH must confirm
PRIMARY_MODEL = "ArcFace"      # First verification
SECONDARY_MODEL = "Facenet"   # Second verification
MODELS = [PRIMARY_MODEL, SECONDARY_MODEL]

# Thresholds for each model (must BOTH pass)
PRIMARY_THRESHOLD = 0.5      # ArcFace threshold
SECONDARY_THRESHOLD = 0.5    # Facenet threshold

# -------------------------------
# YOLOv8 model
# -------------------------------
print("Loading YOLOv8 face detection model...")
model = YOLO(MODEL_PATH)
print("✓ YOLOv8 face detection model loaded!")

# -------------------------------
# DeepFace configuration - Two-Factor Verification
# -------------------------------
print(f"Using Two-Factor Verification: {PRIMARY_MODEL} + {SECONDARY_MODEL}")

# -------------------------------
# Database setup (SQLite) with improved schema
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
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS recognition_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            confidence REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

def save_user_with_multiple_embeddings(embeddings_list, image_paths, name, sr_code, course):
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
            all_embeddings = existing_embeddings + embeddings_list
        else:
            all_embeddings = embeddings_list
        
        embeddings_blob = pickle.dumps(all_embeddings)
        c.execute("""
            UPDATE users 
            SET name = ?, course = ?, embeddings = ?, image_paths = ?, 
                embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (name, course, embeddings_blob, ';'.join(image_paths), len(embeddings_list[0]), user_id))
    else:
        embeddings_blob = pickle.dumps(embeddings_list)
        embedding_dim = len(embeddings_list[0])
        c.execute("""
            INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, sr_code, course, embeddings_blob, ';'.join(image_paths), embedding_dim))
        user_id = c.lastrowid
    
    conn.commit()
    conn.close()
    print(f"✓ User saved/updated with ID: {user_id} ({len(embeddings_list)} embeddings)")
    return user_id

def load_all_embeddings():
    """Load all embeddings for all users"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, name, sr_code, embeddings FROM users")
    rows = c.fetchall()
    conn.close()
    
    all_embeddings = []
    user_info = []
    
    for user_id, name, sr_code, emb_blob in rows:
        if emb_blob:
            embeddings_list = pickle.loads(emb_blob)
            all_embeddings.append(embeddings_list)
            user_info.append({
                'id': user_id,
                'name': name,
                'sr_code': sr_code
            })
    
    print(f"Loaded {len(all_embeddings)} users with embeddings")
    return all_embeddings, user_info

def log_recognition(user_id, confidence):
    """Log recognition events for analysis"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO recognition_log (user_id, confidence) VALUES (?, ?)",
              (user_id, confidence))
    conn.commit()
    conn.close()

# -------------------------------
# Setup directories
# -------------------------------
os.makedirs(BASE_SAVE_DIR, exist_ok=True)

# -------------------------------
# Initialize database
# -------------------------------
init_db()

# Load existing embeddings
all_user_embeddings, user_info = load_all_embeddings()
user_count = len(user_info)

# -------------------------------
# Improved thresholds and parameters
# -------------------------------
BASE_THRESHOLD = 0.3
ADAPTIVE_THRESHOLD_ENABLED = True
FACE_QUALITY_THRESHOLD = 0.2
MIN_FACE_SIZE = 40
CONFIDENCE_SMOOTHING_WINDOW = 2.5

# -------------------------------
# Face quality assessment
# -------------------------------
def assess_face_quality(face_crop):
    """Comprehensive face quality assessment"""
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
    
    if w > 20 and h > 20:
        left_half = gray[:, :w//2]
        right_half = gray[:, w//2:]
        if left_half.shape[1] == right_half.shape[1]:
            symmetry_diff = np.abs(left_half - cv2.flip(right_half, 1))
            symmetry_score = 1.0 - min(np.mean(symmetry_diff) / 50, 1.0)
        else:
            symmetry_score = 0.5
    else:
        symmetry_score = 0.5
    
    quality_score = (
        0.20 * size_score +
        0.15 * brightness_score +
        0.15 * contrast_score +
        0.20 * sharpness_score +
        0.15 * aspect_score +
        0.15 * symmetry_score
    )
    
    quality_status = "Good" if quality_score > FACE_QUALITY_THRESHOLD else "Poor"
    
    return quality_score, quality_status

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
        
        embeddings = []
        
        # Extract embeddings from BOTH models for Two-Factor Verification
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
                if norm > 0:
                    embedding = embedding / norm
                
                embeddings.append((model_name, embedding))
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
        base_threshold = BASE_THRESHOLD
        
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
            self.confidence_smoothing[user_id] = deque(maxlen=CONFIDENCE_SMOOTHING_WINDOW)
        
        self.confidence_smoothing[user_id].append(confidence)
        smoothed = statistics.mean(self.confidence_smoothing[user_id])
        return smoothed
    
    def find_best_match(self, query_embeddings, user_embeddings_list, user_info, face_quality):
        """Find the best match using Two-Factor Verification - BOTH models must confirm"""
        
        # Check if we have embeddings from both models
        model_names = [emb[0] for emb in query_embeddings]
        
        if PRIMARY_MODEL not in model_names or SECONDARY_MODEL not in model_names:
            print(f"  Warning: Need embeddings from both models for 2-factor verification")
            print(f"  Current models: {model_names}")
            return None, []
        
        # Get embeddings for each model
        primary_emb = None
        secondary_emb = None
        for name, emb in query_embeddings:
            if name == PRIMARY_MODEL:
                primary_emb = emb
            elif name == SECONDARY_MODEL:
                secondary_emb = emb
        
        if primary_emb is None or secondary_emb is None:
            return None, []
        
        best_match = None
        best_user_idx = -1
        best_primary_dist = float('inf')
        best_secondary_dist = float('inf')

        # Compare against all users
        for user_idx, user_embeddings in enumerate(user_embeddings_list):
            user_id = user_info[user_idx]['id']
            
            if user_embeddings is None or not isinstance(user_embeddings, (list, np.ndarray)):
                continue
            
            # Find best match for PRIMARY model
            primary_best_dist = float('inf')
            for user_embedding in user_embeddings:
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
            for user_embedding in user_embeddings:
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
            
            primary_pass = primary_confidence >= PRIMARY_THRESHOLD
            secondary_pass = secondary_confidence >= SECONDARY_THRESHOLD
            
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
                        'threshold': (PRIMARY_THRESHOLD + SECONDARY_THRESHOLD) / 2,
                        'user_info': user_info[user_idx]
                    }
                    
                    print(f"  ✓ 2-Factor Verified: {user_info[user_idx]['name']}")
                    print(f"      ArcFace: {primary_confidence:.2%}, Facenet: {secondary_confidence:.2%}")

        if best_match and best_user_idx != -1:
            user_id = user_info[best_user_idx]['id']
            if user_id not in self.recognition_history:
                self.recognition_history[user_id] = deque(maxlen=50)
            self.recognition_history[user_id].append(best_match['confidence'])
            log_recognition(user_id, best_match['confidence'])

        return best_match, []

# Initialize face recognition system
face_recognition_system = FaceRecognitionSystem()

# -------------------------------
# Global variables
# -------------------------------
pending_registration = None
recognized_user = None
registration_in_progress = False
last_processed_face_id = None
captured_faces_for_registration = []
face_capture_count = 0
MAX_CAPTURES_FOR_REGISTRATION = 3

face_stability_tracker = {}
STABILITY_TIME_REQUIRED = 0.5
POSITION_TOLERANCE = 50

# -------------------------------
# Register or Recognize Face
# -------------------------------
def register_or_recognize_face(face_crop, face_id=None):
    global pending_registration, recognized_user, registration_in_progress, last_processed_face_id
    global captured_faces_for_registration, face_capture_count
    
    if registration_in_progress:
        return False
    
    if face_id and face_id == last_processed_face_id:
        return False
    
    quality_score, quality_status = assess_face_quality(face_crop)
    
    if quality_score < FACE_QUALITY_THRESHOLD:
        print(f"  Skipping low quality face: {quality_score:.2f} ({quality_status})")
        return False
    
    print(f"  Face quality: {quality_score:.2f} ({quality_status})")
    
    embeddings = extract_embedding_ensemble(face_crop)
    if not embeddings:
        print("  Failed to extract embeddings")
        return False
    
    best_match, all_distances = face_recognition_system.find_best_match(
        embeddings, all_user_embeddings, user_info, quality_score
    )
    
    if best_match:
        user_idx = best_match['user_idx']
        user_id = best_match['user_info']['id']

        new_embedding = embeddings[0][1]
        all_user_embeddings[user_idx].append(new_embedding)

        timestamp = int(time.time() * 1000)
        user_folder = os.path.join(BASE_SAVE_DIR, best_match['user_info']['sr_code'])
        os.makedirs(user_folder, exist_ok=True)
        filename = os.path.join(user_folder, f"face_{timestamp}_learned.jpg")
        cv2.imwrite(filename, face_crop)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("SELECT embeddings, image_paths FROM users WHERE user_id = ?", (user_id,))
        existing_emb_blob, existing_paths_str = c.fetchone()
        existing_embeddings = pickle.loads(existing_emb_blob) if existing_emb_blob else []
        existing_paths = existing_paths_str.split(';') if existing_paths_str else []

        existing_embeddings.append(new_embedding)
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

        print(f"✓ Learned new embedding for {best_match['user_info']['name']} "
              f"(total: {len(all_user_embeddings[user_idx])} embeddings)")

        recognized_user = {
            'name': best_match['user_info']['name'],
            'sr_code': best_match['user_info']['sr_code'],
            'course': '',
            'confidence': f"{best_match['confidence']:.2%}",
            'distance': f"{best_match['distance']:.4f}"
        }
        print(f"✓ Recognized: {recognized_user['name']} "
              f"(conf: {best_match['confidence']:.2%}, dist: {best_match['distance']:.4f})")
        last_processed_face_id = face_id
        registration_in_progress = False
        return False
    else:
        if not registration_in_progress and len(captured_faces_for_registration) < MAX_CAPTURES_FOR_REGISTRATION:
            captured_faces_for_registration.append({
                'face_crop': face_crop,
                'embeddings': embeddings,
                'quality': quality_score
            })
            face_capture_count += 1
            print(f"  Captured face {face_capture_count}/{MAX_CAPTURES_FOR_REGISTRATION} for registration")
            
            if face_capture_count >= MAX_CAPTURES_FOR_REGISTRATION:
                pending_registration = captured_faces_for_registration.copy()
                registration_in_progress = True
                print(f"✗ New face detected - Ready for registration with {len(pending_registration)} samples")
        
        last_processed_face_id = face_id
        return True

# -------------------------------
# Face stability checking
# -------------------------------
def check_face_stability(face_id, x1, y1, x2, y2):
    """Check if face has been stable for the required time"""
    current_time = time.time()
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    if face_id not in face_stability_tracker:
        face_stability_tracker[face_id] = {
            'positions': [(center_x, center_y)],
            'timestamps': [current_time],
            'stable_since': None
        }
        return False

    tracker = face_stability_tracker[face_id]

    last_x, last_y = tracker['positions'][-1]
    distance = ((center_x - last_x) ** 2 + (center_y - last_y) ** 2) ** 0.5

    if distance <= POSITION_TOLERANCE:
        tracker['positions'].append((center_x, center_y))
        tracker['timestamps'].append(current_time)

        cutoff_time = current_time - 5.0
        valid_indices = [i for i, t in enumerate(tracker['timestamps']) if t >= cutoff_time]
        tracker['positions'] = [tracker['positions'][i] for i in valid_indices]
        tracker['timestamps'] = [tracker['timestamps'][i] for i in valid_indices]

        if len(tracker['timestamps']) >= 2:
            stable_duration = tracker['timestamps'][-1] - tracker['timestamps'][0]
            if stable_duration >= STABILITY_TIME_REQUIRED:
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
def connect_to_cctv_stream(stream_url):
    """Connect to CCTV stream or webcam"""
    print(f"Attempting to connect to: {stream_url}")
    
    # If it's a webcam (0 or "0"), use default backend
    if stream_url == "0":
        cap = cv2.VideoCapture(0)
    else:
        # Try different backend for RTSP streams
        cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            # Try default backend
            cap = cv2.VideoCapture(stream_url)
        
    if cap.isOpened():
        print("✓ Successfully connected!")
        return cap
    else:
        print("✗ Failed to connect")
        return None

def process_cctv_stream(stream_url, frame_width=640, frame_height=480):
    """Process CCTV stream for face recognition"""
    global face_capture_count, recognized_user
    
    camera = connect_to_cctv_stream(stream_url)
    
    if camera is None:
        return
    
    print("\n" + "="*50)
    print("CCTV FACE RECOGNITION SYSTEM")
    print("="*50)
    print("Press 'q' to quit")
    print("Press 'r' to reset recognition status")
    print("="*50)
    
    fps_counter = 0
    fps_start_time = time.time()
    current_fps = 0
    
    while True:
        success, frame = camera.read()
        if not success:
            print("✗ Lost connection to CCTV stream. Reconnecting...")
            camera = connect_to_cctv_stream(stream_url)
            if camera is None:
                break
            continue

        frame = cv2.resize(frame, (frame_width, frame_height))
        results = model(frame, conf=0.3)

        face_crops = []
        face_qualities = []
        stable_faces = []

        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                if (x2 - x1) < MIN_FACE_SIZE or (y2 - y1) < MIN_FACE_SIZE:
                    continue

                face_crop = frame[y1:y2, x1:x2]
                face_crops.append(face_crop)

                quality_score, quality_status = assess_face_quality(face_crop)
                face_qualities.append((quality_score, quality_status))

                face_id = f"{x1}_{y1}_{x2}_{y2}"

                is_stable = check_face_stability(face_id, x1, y1, x2, y2)

                if is_stable:
                    stable_faces.append((face_crop, face_id))

                conf = float(box.conf[0])
                if is_stable:
                    if quality_score > 0.7:
                        color = (0, 255, 0)
                    elif quality_score > 0.5:
                        color = (0, 255, 255)
                    else:
                        color = (0, 0, 255)
                else:
                    color = (128, 128, 128)

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                stability_text = "STABLE" if is_stable else "MOVING"
                cv2.putText(frame, f"{stability_text} Q:{quality_score:.1f}",
                           (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
                           0.5, color, 1)

        for face_crop, face_id in stable_faces:
            if not registration_in_progress:
                register_or_recognize_face(face_crop, face_id)

        # Display thumbnails with quality info
        for i, (crop, (quality_score, quality_status)) in enumerate(zip(face_crops[:5], face_qualities[:5])):
            crop_h, crop_w = crop.shape[:2]
            scale = 80 / crop_h
            thumbnail = cv2.resize(crop, (int(crop_w * scale), 80))
            x_start = 10 + i * 90
            x_end = min(x_start + thumbnail.shape[1], frame_width)
            frame[10:10+thumbnail.shape[0], x_start:x_end] = thumbnail[:, :x_end-x_start]
            
            cv2.putText(frame, f"{quality_score:.1f}",
                       (x_start, 10+thumbnail.shape[0]+15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                       (255, 255, 255), 1)

        # Calculate FPS
        fps_counter += 1
        if time.time() - fps_start_time >= 1.0:
            current_fps = fps_counter
            fps_counter = 0
            fps_start_time = time.time()

        # Display comprehensive status
        status_y = frame_height - 60
        
        # Draw semi-transparent background for status
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, status_y - 30), (frame_width, frame_height), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
        
        if recognized_user:
            status_text = f"Recognized: {recognized_user['name']} ({recognized_user['confidence']})"
            cv2.putText(frame, status_text, (10, status_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        elif registration_in_progress:
            status_text = f"New face - Captured {face_capture_count}/{MAX_CAPTURES_FOR_REGISTRATION} samples"
            cv2.putText(frame, status_text, (10, status_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
        else:
            status_text = f"Detecting... | Users in DB: {user_count} | FPS: {current_fps}"
            cv2.putText(frame, status_text, (10, status_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        threshold_text = f"Threshold: 50% (Fixed)"
        cv2.putText(frame, threshold_text, (10, status_y + 25),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        cv2.imshow('CCTV Face Recognition', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("\nShutting down...")
            break
        elif key == ord('r'):
            recognized_user = None
            print("Recognition status reset")

    camera.release()
    cv2.destroyAllWindows()

# -------------------------------
# Handle Registration (CLI)
# -------------------------------
def handle_registration():
    """Handle new user registration via CLI"""
    global pending_registration, all_user_embeddings, user_info, user_count
    global registration_in_progress, captured_faces_for_registration, face_capture_count

    if not pending_registration:
        print("No pending registration")
        return

    print("\n" + "="*50)
    print("NEW USER REGISTRATION")
    print("="*50)
    print(f"Captured {len(pending_registration)} face samples")
    
    name = input("Enter full name: ").strip()
    sr_code = input("Enter SR Code: ").strip()
    course = input("Enter course: ").strip()

    if not name or not sr_code or not course:
        print("Error: All fields are required!")
        return

    # Check if SR code exists
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE sr_code = ?", (sr_code,))
    existing = c.fetchone()
    conn.close()
    
    if existing:
        print(f"Warning: SR Code {sr_code} already registered to {existing[0]}")
        choice = input("Update existing user? (y/n): ").lower()
        if choice != 'y':
            return

    all_embeddings = []
    image_paths = []
    
    for i, face_data in enumerate(pending_registration):
        timestamp = int(time.time() * 1000)
        user_folder = os.path.join(BASE_SAVE_DIR, sr_code)
        os.makedirs(user_folder, exist_ok=True)
        filename = os.path.join(user_folder, f"face_{timestamp}_{i}.jpg")
        cv2.imwrite(filename, face_data['face_crop'])
        image_paths.append(filename)
        
        if face_data['embeddings']:
            model_name, embedding = face_data['embeddings'][0]
            all_embeddings.append(embedding)
    
    if all_embeddings:
        user_id = save_user_with_multiple_embeddings(all_embeddings, image_paths, name, sr_code, course)
        
        all_user_embeddings.append(all_embeddings)
        user_info.append({
            'id': user_id,
            'name': name,
            'sr_code': sr_code
        })
        user_count = len(user_info)
        
        print(f"✓ Registered {name} with {len(all_embeddings)} embeddings")

    pending_registration = None
    captured_faces_for_registration = []
    face_capture_count = 0
    registration_in_progress = False

# -------------------------------
# Main CLI Interface
# -------------------------------
def main_menu():
    """Main CLI menu for the system"""
    global user_count, all_user_embeddings, user_info
    
    while True:
        print("\n" + "="*50)
        print("CCTV FACE RECOGNITION SYSTEM")
        print("="*50)
        print(f"Users in database: {user_count}")
        print(f"Models: {PRIMARY_MODEL} + {SECONDARY_MODEL}")
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
            stream_url = input("Enter CCTV stream URL (or press Enter for webcam): ").strip()
            if not stream_url:
                stream_url = "0"  # Use default webcam
            process_cctv_stream(stream_url)
            
            # Handle registration if needed
            if registration_in_progress:
                handle_registration()
                
        elif choice == '2':
            print("\nStarting webcam registration...")
            import subprocess
            subprocess.run(["python", "register.py"])
            # Reload embeddings after registration
            global all_user_embeddings, user_info
            all_user_embeddings, user_info = load_all_embeddings()
            user_count = len(user_info)
            
        elif choice == '3':
            print("\n" + "="*50)
            print("REGISTERED USERS")
            print("="*50)
            if not user_info:
                print("No users registered")
            else:
                for info in user_info:
                    print(f"ID: {info['id']:3d} | Name: {info['name']:20s} | SR Code: {info['sr_code']}")
            print("="*50)
            
        elif choice == '4':
            try:
                user_id = int(input("Enter User ID to delete (0 to cancel): "))
                if user_id == 0:
                    continue
                    
                conn = sqlite3.connect(DB_PATH)
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
                    all_user_embeddings, user_info = load_all_embeddings()
                    user_count = len(user_info)
                    
                conn.close()
            except ValueError:
                print("Invalid input")
                
        elif choice == '5':
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT u.name, COUNT(r.log_id) as recognitions, 
                       AVG(r.confidence) as avg_confidence
                FROM users u
                LEFT JOIN recognition_log r ON u.user_id = r.user_id
                GROUP BY u.user_id
                ORDER BY recognitions DESC
            """)
            stats = c.fetchall()
            conn.close()
            
            print("\n" + "="*50)
            print("RECOGNITION STATISTICS")
            print("="*50)
            if not stats:
                print("No recognition data")
            else:
                for name, rec_count, avg_conf in stats:
                    avg = f"{avg_conf:.2%}" if avg_conf else "N/A"
                    print(f"{name:20s} | Recognitions: {rec_count:5d} | Avg Confidence: {avg}")
            print("="*50)
            
        elif choice == '6':
            print("\n⚠️  WARNING: This will delete ALL users and data!")
            confirm = input("Type 'YES' to confirm: ")
            if confirm == 'YES':
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("DELETE FROM users")
                c.execute("DELETE FROM recognition_log")
                conn.commit()
                conn.close()
                
                import shutil
                if os.path.exists(BASE_SAVE_DIR):
                    shutil.rmtree(BASE_SAVE_DIR)
                
                all_user_embeddings = []
                user_info = []
                user_count = 0
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
    print(f"\n=== CCTV Face Recognition System ===")
    print(f"Database: {DB_PATH}")
    print(f"Face models: {PRIMARY_MODEL} + {SECONDARY_MODEL}")
    print(f"Base threshold: {BASE_THRESHOLD}")
    print(f"Users in database: {user_count}")
    print("="*50)
    
    main_menu()

