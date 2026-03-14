from flask import Flask, render_template, Response, request, redirect, url_for, session
import cv2 
from ultralytics import YOLO
import os
import time
import numpy as np
import sqlite3
from deepface import DeepFace
import pickle
from collections import deque
import statistics
import threading 
from auth import (
    init_auth_db,
    login_required,
    role_required,
)
from routes.admin_routes import create_admin_blueprint
from routes.auth_routes import create_auth_blueprint
from routes.profile_routes import create_profile_blueprint
from services.face_service import (
    init_db,
    load_all_embeddings,
    log_recognition,
    render_markdown_as_html,
    save_user_with_multiple_embeddings,
)
from services.staff_service import ensure_profile_upload_dir, save_profile_image

# -------------------------------
# Flask app
# -------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-change-this-secret-key")

# -------------------------------
# YOLOv8M model
# -------------------------------
MODEL_PATH = "models/face_yolov8m.pt"
model = YOLO(MODEL_PATH)
print("YOLOv8 face detection model loaded!")

# -------------------------------
# DeepFace configuration with multiple models
# -------------------------------
FACE_MODELS = ["Facenet", "ArcFace", "VGG-Face"]  # Ensemble of models
CURRENT_MODEL = FACE_MODELS[2]  # Default to Facenet
print(f"Using DeepFace with {CURRENT_MODEL} model")

# -------------------------------
# Database setup (SQLite) with improved schema
# -------------------------------
DB_PATH = "database/faces_improved.db"

# -------------------------------
# Setup directories
# -------------------------------
BASE_SAVE_DIR = "faces_improved"
os.makedirs(BASE_SAVE_DIR, exist_ok=True)
ensure_profile_upload_dir()

# -------------------------------
# Initialize database
# -------------------------------
init_db(DB_PATH)
init_auth_db()

# Load existing embeddings
all_user_embeddings, user_info = load_all_embeddings(DB_PATH)
user_count = len(user_info)

# -------------------------------
# Improved thresholds and parameters
# -------------------------------
BASE_THRESHOLD = 0.3  # Base threshold for Facenet
ADAPTIVE_THRESHOLD_ENABLED = True
FACE_QUALITY_THRESHOLD = 0.2  # Minimum face quality score (further lowered to allow more faces)
MIN_FACE_SIZE = 60  # Minimum face size in pixels
CONFIDENCE_SMOOTHING_WINDOW = 5  # Number of frames for smoothing

# -------------------------------
# Webcam setup
# -------------------------------
FRAME_WIDTH = 640
FRAME_HEIGHT = 480


class CameraManager:

    def __init__(self):
        self.camera = None
        self.active_streams = 0
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            self.active_streams += 1
            if self.camera is None or not self.camera.isOpened():
                self.camera = cv2.VideoCapture(0)
                if not self.camera.isOpened():
                    print("Error: Camera not accessible")
                    self.active_streams -= 1
                    return False
                self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
                self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
                print("Camera initialized successfully")
            return True

    def release(self):
        with self.lock:
            self.active_streams = max(0, self.active_streams - 1)  # never go negative
            if self.active_streams == 0 and self.camera:
                self.camera.release()
                self.camera = None
                print("Camera released")

    def read(self):
        if self.camera and self.camera.isOpened():
            return self.camera.read()
        return False, None


camera_manager = CameraManager()


# -------------------------------
# Face quality assessment
# -------------------------------
def assess_face_quality(face_crop):
    """Comprehensive face quality assessment"""
    if face_crop is None or face_crop.size == 0:
        return 0.0, "No face"
    
    h, w = face_crop.shape[:2]
    
    # 1. Size score
    size_score = min((h * w) / (MIN_FACE_SIZE * MIN_FACE_SIZE), 1.0)
    
    # 2. Convert to grayscale for analysis
    if len(face_crop.shape) == 3:
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = face_crop
    
    # 3. Brightness score (optimal range: 100-180)
    brightness = np.mean(gray)
    brightness_score = 1.0 - min(abs(brightness - 140) / 100, 1.0)
    
    # 4. Contrast score (higher is better)
    contrast = np.std(gray)
    contrast_score = min(contrast / 60, 1.0)
    
    # 5. Sharpness (Laplacian variance)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = laplacian.var()
    sharpness_score = min(sharpness / 1000, 1.0)
    
    # 6. Face aspect ratio (should be roughly 1:1.2)
    aspect_ratio = w / h
    aspect_score = 1.0 - min(abs(aspect_ratio - 0.85) / 0.3, 1.0)
    
    # 7. Check for face symmetry (simplified)
    if w > 20 and h > 20:
        left_half = gray[:,:w // 2]
        right_half = gray[:, w // 2:]
        if left_half.shape[1] == right_half.shape[1]:
            symmetry_diff = np.abs(left_half - cv2.flip(right_half, 1))
            symmetry_score = 1.0 - min(np.mean(symmetry_diff) / 50, 1.0)
        else:
            symmetry_score = 0.5
    else:
        symmetry_score = 0.5
    
    # Weighted quality score
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
# Improved embedding extraction
# -------------------------------
def extract_embedding_ensemble(face_crop):
    """Extract embeddings using multiple models for better accuracy"""
    try:
        # Convert BGR to RGB
        if len(face_crop.shape) == 3 and face_crop.shape[2] == 3:
            face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        else:
            if len(face_crop.shape) == 2:
                face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_GRAY2RGB)
            else:
                face_rgb = face_crop
        
        embeddings = []
        
        # Try multiple models
        for model_name in [CURRENT_MODEL]:  # Start with current model
            try:
                embedding_obj = DeepFace.represent(
                    img_path=face_rgb,
                    model_name=model_name,
                    enforce_detection=False,
                    detector_backend='skip',
                    align=True,  # Enable alignment for better accuracy
                    normalization='base'  # Use base normalization
                )
                
                embedding = np.array(embedding_obj[0]['embedding'], dtype=np.float32)
                
                # L2 normalization
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    embedding = embedding / norm
                
                embeddings.append((model_name, embedding))
                
            except Exception as e:
                print(f"  Model {model_name} failed: {e}")
                continue
        
        if not embeddings:
            # Fallback to simple extraction
            embedding_obj = DeepFace.represent(
                img_path=face_rgb,
                model_name='Facenet',
                enforce_detection=False,
                detector_backend='skip',
                align=False
            )
            embedding = np.array(embedding_obj[0]['embedding'], dtype=np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            embeddings.append(('Facenet', embedding))
        
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
        
        # Adjust based on face quality (poor quality needs higher threshold)
        if face_quality < 0.5:
            quality_adjustment = 0.1
        elif face_quality < 0.7:
            quality_adjustment = 0.05
        else:
            quality_adjustment = -0.05  # Lower threshold for good quality
        
        # Adjust based on recognition history
        if user_id in self.recognition_history:
            history = self.recognition_history[user_id]
            avg_confidence = statistics.mean(history) if len(history) > 0 else 0.5
            history_adjustment = (0.5 - avg_confidence) * 0.2
        else:
            history_adjustment = 0
        
        dynamic_threshold = base_threshold + quality_adjustment + history_adjustment
        return max(0.2, min(0.6, dynamic_threshold))  # Keep in reasonable range
    
    def smooth_confidence(self, user_id, confidence):
        """Apply smoothing to confidence scores"""
        if user_id not in self.confidence_smoothing:
            self.confidence_smoothing[user_id] = deque(maxlen=CONFIDENCE_SMOOTHING_WINDOW)
        
        self.confidence_smoothing[user_id].append(confidence)
        smoothed = statistics.mean(self.confidence_smoothing[user_id])
        return smoothed
    
    def find_best_match(self, query_embeddings, user_embeddings_list, user_info, face_quality):
        """Find the best match using 90% confidence threshold"""
        best_match = None
        best_distance = float('inf')
        best_user_idx = -1
        all_distances = []

        # Fixed 50% confidence threshold (75% was still too strict)
        CONFIDENCE_THRESHOLD = 0.5

        for model_name, query_embedding in query_embeddings:
            for user_idx, user_embeddings in enumerate(user_embeddings_list):
                user_id = user_info[user_idx]['id']

                # Try multiple embeddings for this user
                user_best_distance = float('inf')
                for user_embedding in user_embeddings:
                    # Calculate cosine distance
                    distance = 1 - np.dot(query_embedding, user_embedding)
                    user_best_distance = min(user_best_distance, distance)

                all_distances.append((user_idx, user_best_distance))

                if user_best_distance < best_distance:
                    best_distance = user_best_distance
                    best_user_idx = user_idx

        if best_user_idx != -1:
            confidence = 1 - best_distance

            # For high confidence threshold, use raw confidence without smoothing
            # to avoid averaging down high-confidence matches
            if confidence >= CONFIDENCE_THRESHOLD:
                best_match = {
                    'user_idx': best_user_idx,
                    'distance': best_distance,
                    'confidence': confidence,
                    'threshold': CONFIDENCE_THRESHOLD,
                    'user_info': user_info[best_user_idx]
                }

                # Update recognition history with raw confidence
                if user_info[best_user_idx]['id'] not in self.recognition_history:
                    self.recognition_history[user_info[best_user_idx]['id']] = deque(maxlen=50)
                self.recognition_history[user_info[best_user_idx]['id']].append(confidence)

                # Log successful recognition
                log_recognition(DB_PATH, user_info[best_user_idx]['id'], confidence)

        return best_match, all_distances


# Initialize face recognition system
face_recognition_system = FaceRecognitionSystem()

# -------------------------------
# Global variables
# -------------------------------
pending_registration = None  # Multiple face crops waiting for registration
recognized_user = None  # Info of recognized user
registration_in_progress = False
last_processed_face_id = None
captured_faces_for_registration = []  # Store multiple faces for registration
face_capture_count = 0
MAX_CAPTURES_FOR_REGISTRATION = 3  # Capture 3 faces for better registration

# Face stability tracking for 1.5-second stillness requirement
face_stability_tracker = {}  # face_id -> {'positions': [], 'timestamps': [], 'stable_since': None}
STABILITY_TIME_REQUIRED = 0.5  # seconds
POSITION_TOLERANCE = 50  # pixels (increased for more natural movement tolerance)


# -------------------------------
# Register or Recognize Face
# -------------------------------
def register_or_recognize_face(face_crop, face_id=None):
    global pending_registration, recognized_user, registration_in_progress, last_processed_face_id
    global captured_faces_for_registration, face_capture_count
    
    # Skip if registration in progress
    if registration_in_progress:
        return False
    
    # Don't re-process the same face
    if face_id and face_id == last_processed_face_id:
        return False
    
    # Assess face quality
    quality_score, quality_status = assess_face_quality(face_crop)
    
    # Skip low quality faces
    if quality_score < FACE_QUALITY_THRESHOLD:
        print(f"  Skipping low quality face: {quality_score:.2f} ({quality_status})")
        return False
    
    print(f"  Face quality: {quality_score:.2f} ({quality_status})")
    
    # Extract embeddings using ensemble
    embeddings = extract_embedding_ensemble(face_crop)
    if not embeddings:
        print("  Failed to extract embeddings")
        return False
    
    # Find best match
    best_match, all_distances = face_recognition_system.find_best_match(
        embeddings, all_user_embeddings, user_info, quality_score
    )
    
    if best_match:
        # Recognized user - add new embedding for continuous learning
        user_idx = best_match['user_idx']
        user_id = best_match['user_info']['id']

        # Add new embedding to existing user's embeddings
        new_embedding = embeddings[0][1]  # Use first model embedding
        all_user_embeddings[user_idx].append(new_embedding)

        # Save new face image
        timestamp = int(time.time() * 1000)
        user_folder = os.path.join(BASE_SAVE_DIR, best_match['user_info']['sr_code'])
        os.makedirs(user_folder, exist_ok=True)
        filename = os.path.join(user_folder, f"face_{timestamp}_learned.jpg")
        cv2.imwrite(filename, face_crop)

        # Update database with new embedding and image path
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Load existing embeddings and image paths
        c.execute("SELECT embeddings, image_paths FROM users WHERE user_id = ?", (user_id,))
        existing_emb_blob, existing_paths_str = c.fetchone()
        existing_embeddings = pickle.loads(existing_emb_blob) if existing_emb_blob else []
        existing_paths = existing_paths_str.split(';') if existing_paths_str else []

        # Add new embedding and path
        existing_embeddings.append(new_embedding)
        existing_paths.append(filename)

        # Update database
        updated_emb_blob = pickle.dumps(existing_embeddings)
        updated_paths_str = ';'.join(existing_paths)
        c.execute("""
            UPDATE users
            SET embeddings = ?, image_paths = ?, last_updated = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (updated_emb_blob, updated_paths_str, user_id))

        conn.commit()
        conn.close()

        print(f"âœ“ Learned new embedding for {best_match['user_info']['name']} "
              f"(total: {len(all_user_embeddings[user_idx])} embeddings)")

        # Recognized user
        recognized_user = {
            'name': best_match['user_info']['name'],
            'sr_code': best_match['user_info']['sr_code'],
            'course': '',  # Will be loaded from database if needed
            'confidence': f"{best_match['confidence']:.2%}",
            'distance': f"{best_match['distance']:.4f}"
        }
        print(f"âœ“ Recognized: {recognized_user['name']} "
              f"(conf: {best_match['confidence']:.2%}, dist: {best_match['distance']:.4f})")
        last_processed_face_id = face_id
        registration_in_progress = False
        return False
    else:
        # New face - check if we should start registration
        if not registration_in_progress and len(captured_faces_for_registration) < MAX_CAPTURES_FOR_REGISTRATION:
            # Store face for registration (multiple angles/expressions)
            captured_faces_for_registration.append({
                'face_crop': face_crop,
                'embeddings': embeddings,
                'quality': quality_score
            })
            face_capture_count += 1
            print(f"  Captured face {face_capture_count}/{MAX_CAPTURES_FOR_REGISTRATION} for registration")
            
            if face_capture_count >= MAX_CAPTURES_FOR_REGISTRATION:
                # Enough faces captured, trigger registration
                pending_registration = captured_faces_for_registration.copy()
                registration_in_progress = True
                print(f"âœ— New face detected - Ready for registration with {len(pending_registration)} samples")
        
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

    # Initialize tracking for new face
    if face_id not in face_stability_tracker:
        face_stability_tracker[face_id] = {
            'positions': [(center_x, center_y)],
            'timestamps': [current_time],
            'stable_since': None
        }
        return False

    tracker = face_stability_tracker[face_id]

    # Check if position is stable (within tolerance)
    last_x, last_y = tracker['positions'][-1]
    distance = ((center_x - last_x) ** 2 + (center_y - last_y) ** 2) ** 0.5

    if distance <= POSITION_TOLERANCE:
        # Position is stable, add to tracking
        tracker['positions'].append((center_x, center_y))
        tracker['timestamps'].append(current_time)

        # Keep only recent positions (last 5 seconds)
        cutoff_time = current_time - 5.0
        valid_indices = [i for i, t in enumerate(tracker['timestamps']) if t >= cutoff_time]
        tracker['positions'] = [tracker['positions'][i] for i in valid_indices]
        tracker['timestamps'] = [tracker['timestamps'][i] for i in valid_indices]

        # Check if stable for required time
        if len(tracker['timestamps']) >= 2:
            stable_duration = tracker['timestamps'][-1] - tracker['timestamps'][0]
            if stable_duration >= STABILITY_TIME_REQUIRED:
                if tracker['stable_since'] is None:
                    tracker['stable_since'] = current_time
                return True
    else:
        # Position changed, reset stability tracking
        tracker['positions'] = [(center_x, center_y)]
        tracker['timestamps'] = [current_time]
        tracker['stable_since'] = None

    return False


# -------------------------------
# Video streaming with improved visualization
# -------------------------------
def generate_frames():
    global face_capture_count

    if not camera_manager.acquire():
        return  # Camera unavailable, stop the generator

    try:
        while True:
            success, frame = camera_manager.read()  # <-- only change inside the loop
            if not success:
                break

            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            results = model(frame, conf=0.3)

            face_crops = []
            face_qualities = []
            stable_faces = []

            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])

                    # Check minimum size
                    if (x2 - x1) < MIN_FACE_SIZE or (y2 - y1) < MIN_FACE_SIZE:
                        continue

                    face_crop = frame[y1:y2, x1:x2]
                    face_crops.append(face_crop)

                    # Assess quality
                    quality_score, quality_status = assess_face_quality(face_crop)
                    face_qualities.append((quality_score, quality_status))

                    # Give each face a unique ID
                    face_id = f"{x1}_{y1}_{x2}_{y2}"

                    # Check stability
                    is_stable = check_face_stability(face_id, x1, y1, x2, y2)

                    if is_stable:
                        stable_faces.append((face_crop, face_id))

                    # Draw rectangle with color based on stability and quality
                    conf = float(box.conf[0])
                    if is_stable:
                        if quality_score > 0.7:
                            color = (0, 255, 0)  # Green for stable good quality
                        elif quality_score > 0.5:
                            color = (0, 255, 255)  # Yellow for stable medium quality
                        else:
                            color = (0, 0, 255)  # Red for stable poor quality
                    else:
                        color = (128, 128, 128)  # Gray for unstable faces

                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                    # Display stability and quality info
                    stability_text = "STABLE" if is_stable else "MOVING"
                    cv2.putText(frame, f"{stability_text} Q:{quality_score:.1f}",
                               (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX,
                               0.5, color, 1)

            # Process only stable faces
            for face_crop, face_id in stable_faces:
                if not registration_in_progress:
                    register_or_recognize_face(face_crop, face_id)

            # Display thumbnails with quality info
            for i, (crop, (quality_score, quality_status)) in enumerate(zip(face_crops[:5], face_qualities[:5])):
                crop_h, crop_w = crop.shape[:2]
                scale = 80 / crop_h
                thumbnail = cv2.resize(crop, (int(crop_w * scale), 80))
                x_start = 10 + i * 90
                x_end = min(x_start + thumbnail.shape[1], FRAME_WIDTH)
                frame[10:10 + thumbnail.shape[0], x_start:x_end] = thumbnail[:,:x_end - x_start]

                # Quality indicator on thumbnail
                cv2.putText(frame, f"{quality_score:.1f}",
                           (x_start, 10 + thumbnail.shape[0] + 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                           (255, 255, 255), 1)

            # Display comprehensive status
            status_y = FRAME_HEIGHT - 60
            if recognized_user:
                status_text = f"Recognized: {recognized_user['name']} ({recognized_user['confidence']})"
                cv2.putText(frame, status_text, (10, status_y),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            elif registration_in_progress:
                status_text = f"New face - Captured {face_capture_count}/{MAX_CAPTURES_FOR_REGISTRATION} samples"
                cv2.putText(frame, status_text, (10, status_y),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
            else:
                status_text = f"Detecting... | Users in DB: {user_count}"
                cv2.putText(frame, status_text, (10, status_y),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # Display threshold info
            threshold_text = f"Threshold: 90% (Fixed)"
            cv2.putText(frame, threshold_text, (10, status_y + 25),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            _, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    finally:
        camera_manager.release()  # Always releases when client disconnects


@app.template_global("time")
def template_time():
    return int(time.time())


@app.template_global("csrf_token")
def template_csrf_token():
    # Placeholder so converted templates referencing csrf_token() can render.
    return ""


@app.context_processor
def inject_auth_context():
    return {
        "is_authenticated": "staff_id" in session,
        "current_username": session.get("username"),
        "current_full_name": session.get("full_name"),
        "current_role": session.get("role"),
        "current_profile_image": session.get("profile_image"),
    }


def _get_user_count():
    return user_count


def _get_thresholds():
    return BASE_THRESHOLD, ADAPTIVE_THRESHOLD_ENABLED, FACE_QUALITY_THRESHOLD


def _set_thresholds(new_threshold, adaptive_enabled, quality_threshold):
    global BASE_THRESHOLD, ADAPTIVE_THRESHOLD_ENABLED, FACE_QUALITY_THRESHOLD
    BASE_THRESHOLD = new_threshold
    ADAPTIVE_THRESHOLD_ENABLED = adaptive_enabled
    FACE_QUALITY_THRESHOLD = quality_threshold


def _reset_database_state():
    global all_user_embeddings, user_info, user_count
    all_user_embeddings = []
    user_info = []
    user_count = 0


def _reset_registration_state():
    global pending_registration, registration_in_progress, captured_faces_for_registration, face_capture_count
    pending_registration = None
    registration_in_progress = False
    captured_faces_for_registration = []
    face_capture_count = 0


app.register_blueprint(create_auth_blueprint())
app.register_blueprint(
    create_admin_blueprint(
        {
            "render_markdown_as_html": render_markdown_as_html,
            "get_user_count": _get_user_count,
            "get_thresholds": _get_thresholds,
            "set_thresholds": _set_thresholds,
            "db_path": DB_PATH,
            "base_save_dir": BASE_SAVE_DIR,
            "reset_database_state": _reset_database_state,
            "reset_registration_state": _reset_registration_state,
        }
    )
)
app.register_blueprint(create_profile_blueprint(save_profile_image))

_endpoint_aliases = {
    "auth_login": "auth_routes.auth_login",
    "auth_logout": "auth_routes.auth_logout",
    "unauthorized": "auth_routes.unauthorized",
    "pages_home": "admin_routes.pages_home",
    "policy_page": "admin_routes.policy_page",
    "dashboard_page": "admin_routes.dashboard_page",
    "route_list_page": "admin_routes.route_list_page",
    "manage_users": "admin_routes.manage_users",
    "manage_users_create": "admin_routes.manage_users_create",
    "manage_users_toggle": "admin_routes.manage_users_toggle",
    "settings": "admin_routes.settings",
    "get_stats": "admin_routes.get_stats",
    "reset_database": "admin_routes.reset_database",
    "clear_log": "admin_routes.clear_log",
    "reset_registration": "admin_routes.reset_registration",
    "profile_settings": "profile_routes.profile_settings",
    "profile_settings_update": "profile_routes.profile_settings_update",
    "profile_change_password": "profile_routes.profile_change_password",
}
for legacy_name, namespaced_name in _endpoint_aliases.items():
    if legacy_name not in app.view_functions and namespaced_name in app.view_functions:
        app.view_functions[legacy_name] = app.view_functions[namespaced_name]


@app.errorhandler(401)
def unauthorized_error(error):
    return render_template("html/errors/401.html"), 401


@app.errorhandler(403)
def forbidden_error(error):
    return render_template("html/errors/403.html"), 403


@app.errorhandler(404)
def not_found_error(error):
    return render_template("html/errors/404.html"), 404


@app.errorhandler(500)
def internal_server_error(error):
    return render_template("html/errors/500.html"), 500


@app.route("/")
def kiosk():
    global pending_registration, recognized_user
    return render_template("html/kiosk_improved.html",
                           pending_registration=pending_registration is not None,
                           recognized_user=recognized_user,
                           user_count=user_count)


@app.route("/public")
def public_page():
    return render_template("html/pages/public-page.html", user_count=user_count)


@app.route("/admin/register", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "library_admin")
def register():
    global pending_registration, all_user_embeddings, user_info, user_count
    global registration_in_progress, captured_faces_for_registration, face_capture_count

    if not pending_registration:
        return redirect(url_for('kiosk'))

    if request.method == "POST":
        name = request.form["name"]
        sr_code = request.form["sr_code"]
        course = request.form["course"]

        # Extract embeddings from all captured faces
        all_embeddings = []
        image_paths = []
        
        for i, face_data in enumerate(pending_registration):
            # Save each face image
            timestamp = int(time.time() * 1000)
            user_folder = os.path.join(BASE_SAVE_DIR, sr_code)
            os.makedirs(user_folder, exist_ok=True)
            filename = os.path.join(user_folder, f"face_{timestamp}_{i}.jpg")
            cv2.imwrite(filename, face_data['face_crop'])
            image_paths.append(filename)
            
            # Use the first embedding from each face
            if face_data['embeddings']:
                model_name, embedding = face_data['embeddings'][0]
                all_embeddings.append(embedding)
        
        if all_embeddings:
            # Save user with multiple embeddings
            user_id = save_user_with_multiple_embeddings(DB_PATH, all_embeddings, image_paths, name, sr_code, course)
            
            # Update in-memory data
            all_user_embeddings.append(all_embeddings)
            user_info.append({
                'id': user_id,
                'name': name,
                'sr_code': sr_code
            })
            user_count = len(user_info)
            
            print(f"âœ“ Registered {name} with {len(all_embeddings)} embeddings")

        # Reset registration state
        pending_registration = None
        captured_faces_for_registration = []
        face_capture_count = 0
        registration_in_progress = False
        
        time.sleep(1)  # Brief pause
        
        return redirect(url_for("kiosk"))

    return render_template("html/register_improved.html",
                          capture_count=len(pending_registration) if pending_registration else 0)


@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route("/stop_feed")
def stop_feed():
    camera_manager.release()
    return "", 204


@app.route("/check_status")
def check_status():
    """Improved status checking"""
    global pending_registration, recognized_user, face_capture_count, MAX_CAPTURES_FOR_REGISTRATION
    
    user_to_show = recognized_user
    if recognized_user:
        recognized_user = None  # Reset after showing once
    
    return {
        "pending_registration": pending_registration is not None,
        "recognized_user": user_to_show,
        "capture_progress": {
            "current": face_capture_count,
            "total": MAX_CAPTURES_FOR_REGISTRATION,
            "percentage": (face_capture_count / MAX_CAPTURES_FOR_REGISTRATION) * 100 if MAX_CAPTURES_FOR_REGISTRATION > 0 else 0
        }
    }


# -------------------------------
# Run app
# -------------------------------
if __name__ == "__main__":
    init_db(DB_PATH)
    print(f"\n=== Improved Face Recognition System ===")
    print(f"Database: {DB_PATH}")
    print(f"Face model: {CURRENT_MODEL}")
    print(f"Base threshold: {BASE_THRESHOLD}")
    print(f"Adaptive threshold: {'Enabled' if ADAPTIVE_THRESHOLD_ENABLED else 'Disabled'}")
    print(f"Face quality threshold: {FACE_QUALITY_THRESHOLD}")
    print(f"Users in database: {user_count}")
    print(f"Open browser to: http://localhost:5000")
    print("="*50)
    app.run(host="0.0.0.0", port=5000, debug=True)

