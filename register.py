import cv2
import numpy as np
import sqlite3
import os
import time
from ultralytics import YOLO
from deepface import DeepFace

# -------------------------------
# Configuration
# -------------------------------
MODEL_PATH = "yolov8m.pt"
DB_PATH = "faces.db"
BASE_SAVE_DIR = "faces"
FACE_MODEL = "Facenet"  # Options: 'VGG-Face', 'Facenet', 'OpenFace', 'DeepID', 'ArcFace'
THRESHOLD = 0.4  # Lower threshold for DeepFace Facenet (cosine distance)

# -------------------------------
# Initialize models
# -------------------------------
print("Loading YOLOv8 model...")
yolo_model = YOLO(MODEL_PATH)
print("✓ YOLOv8 loaded")

print(f"\nInitializing DeepFace with {FACE_MODEL} model...")
print("Note: First time will download the model (approx 90MB)")

# -------------------------------
# Database functions
# -------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            sr_code TEXT,
            course TEXT,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            image_path TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized")

def save_user(embedding, image_path, name, sr_code, course):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    embedding_dim = len(embedding)
    c.execute("""
        INSERT INTO users (name, sr_code, course, embedding, embedding_dim, image_path) 
        VALUES (?, ?, ?, ?, ?, ?)""",
        (name, sr_code, course, embedding.tobytes(), embedding_dim, image_path))
    user_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"User saved with ID: {user_id}")
    return user_id

def load_embeddings():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if embedding_dim column exists, if not add it
    try:
        c.execute("SELECT user_id, name, embedding, embedding_dim FROM users")
    except sqlite3.OperationalError:
        # Add the embedding_dim column if it doesn't exist
        print("Adding embedding_dim column to database...")
        c.execute("ALTER TABLE users ADD COLUMN embedding_dim INTEGER DEFAULT 512")
        conn.commit()
        c.execute("SELECT user_id, name, embedding FROM users")
        rows = c.fetchall()
        # Update existing rows with default dimension
        for user_id, name, emb_blob in rows:
            emb = np.frombuffer(emb_blob, dtype=np.float32)
            c.execute("UPDATE users SET embedding_dim = ? WHERE user_id = ?", 
                     (len(emb), user_id))
        conn.commit()
        c.execute("SELECT user_id, name, embedding, embedding_dim FROM users")
    
    rows = c.fetchall()
    conn.close()
    
    embeddings = []
    ids = []
    names = []
    dims = []
    
    for user_id, name, emb_blob, embedding_dim in rows:
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        
        # Check if embedding dimension matches what's in database
        if len(emb) != embedding_dim:
            print(f"⚠️ Warning: User {user_id} ({name}) embedding dimension mismatch: "
                  f"stored={embedding_dim}, actual={len(emb)}. Fixing...")
            # Update the database with correct dimension
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE users SET embedding_dim = ? WHERE user_id = ?", 
                     (len(emb), user_id))
            conn.commit()
            conn.close()
            embedding_dim = len(emb)
        
        embeddings.append(emb)
        ids.append(user_id)
        names.append(name)
        dims.append(embedding_dim)
    
    if embeddings:
        print(f"Loaded {len(embeddings)} existing embeddings")
        print(f"Embedding dimensions found: {set(dims)}")
    else:
        print("No embeddings found in database")
    
    return ids, names, embeddings, dims

def check_if_face_exists(new_embedding, existing_embeddings, threshold=THRESHOLD):
    """Check if a face already exists in the database"""
    if len(existing_embeddings) == 0:
        return False, -1, None
    
    min_dist = float('inf')
    min_idx = -1
    
    new_embedding_dim = len(new_embedding)
    
    for idx, saved_emb in enumerate(existing_embeddings):
        saved_emb_dim = len(saved_emb)
        
        # Skip if dimensions don't match
        if new_embedding_dim != saved_emb_dim:
            print(f"  Skipping user {idx}: dimension mismatch "
                  f"(new: {new_embedding_dim}, saved: {saved_emb_dim})")
            continue
        
        # Calculate cosine distance (1 - cosine similarity)
        # For normalized vectors, cosine similarity = dot product
        dot_product = np.dot(new_embedding, saved_emb)
        dist = 1 - dot_product  # Cosine distance
        
        if dist < min_dist:
            min_dist = dist
            min_idx = idx
    
    print(f"Minimum cosine distance to existing faces: {min_dist:.4f}")
    if min_dist < threshold and min_idx != -1:
        return True, min_idx, min_dist
    return False, min_idx, min_dist

# -------------------------------
# DeepFace functions
# -------------------------------
def extract_embedding_deepface(face_crop):
    """Extract face embedding using DeepFace"""
    try:
        # Convert BGR to RGB (DeepFace expects RGB)
        if len(face_crop.shape) == 3 and face_crop.shape[2] == 3:
            face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
        else:
            # If already grayscale or single channel, convert to 3 channels
            if len(face_crop.shape) == 2:
                face_rgb = cv2.cvtColor(face_crop, cv2.COLOR_GRAY2RGB)
            else:
                face_rgb = face_crop
        
        # Get embedding using DeepFace
        # enforce_detection=False because we're already providing cropped faces
        # detector_backend='skip' to skip face detection since we already have faces
        embedding_obj = DeepFace.represent(
            img_path=face_rgb,
            model_name=FACE_MODEL,
            enforce_detection=False,
            detector_backend='skip',
            align=False
        )
        
        # Extract the embedding array
        embedding = np.array(embedding_obj[0]['embedding'], dtype=np.float32)
        
        # Normalize the embedding (L2 normalization)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        
        return embedding
        
    except Exception as e:
        print(f"Error extracting embedding with DeepFace: {e}")
        return None

def migrate_old_embeddings():
    """Convert old 512-dim embeddings to 128-dim if needed"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check for old 512-dim embeddings
    c.execute("SELECT user_id, name, embedding, image_path FROM users WHERE embedding_dim = 512 OR embedding_dim IS NULL")
    old_users = c.fetchall()
    
    if not old_users:
        print("No old embeddings to migrate")
        conn.close()
        return
    
    print(f"\nFound {len(old_users)} users with old 512-dim embeddings")
    print("Migrating to DeepFace 128-dim embeddings...")
    
    migrated_count = 0
    for user_id, name, emb_blob, image_path in old_users:
        try:
            # Load the old embedding
            old_emb = np.frombuffer(emb_blob, dtype=np.float32)
            
            if len(old_emb) == 512:
                print(f"  User {user_id} ({name}): Old 512-dim embedding")
                
                # Try to load the face image and extract new embedding
                if os.path.exists(image_path):
                    face_img = cv2.imread(image_path)
                    if face_img is not None:
                        new_emb = extract_embedding_deepface(face_img)
                        if new_emb is not None:
                            # Update with new embedding
                            c.execute("UPDATE users SET embedding = ?, embedding_dim = ? WHERE user_id = ?",
                                     (new_emb.tobytes(), len(new_emb), user_id))
                            migrated_count += 1
                            print(f"    → Migrated to {len(new_emb)}-dim embedding")
                        else:
                            print(f"    ✗ Could not extract new embedding")
                    else:
                        print(f"    ✗ Image not found: {image_path}")
                else:
                    print(f"    ✗ Image path doesn't exist")
            else:
                print(f"  User {user_id} ({name}): Already {len(old_emb)}-dim, skipping")
                
        except Exception as e:
            print(f"  ✗ Error migrating user {user_id}: {e}")
    
    conn.commit()
    conn.close()
    
    if migrated_count > 0:
        print(f"\n✅ Migrated {migrated_count} users to DeepFace embeddings")
    else:
        print("\n⚠️ No users migrated")

# -------------------------------
# Face processing functions
# -------------------------------
def calculate_face_quality(face_crop):
    """Calculate a simple quality score for face"""
    if face_crop is None or face_crop.size == 0:
        return 0
    
    h, w = face_crop.shape[:2]
    
    # Size score - prefer larger faces
    size_score = min(h * w / (100 * 100), 1.0)
    
    # Brightness score - avoid too dark or too bright
    if len(face_crop.shape) == 3:
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = face_crop
    
    brightness = np.mean(gray)
    brightness_score = 1 - abs(brightness - 128) / 128
    
    # Contrast score
    contrast = np.std(gray)
    contrast_score = min(contrast / 64, 1.0)
    
    # Overall quality
    quality = 0.4 * size_score + 0.3 * brightness_score + 0.3 * contrast_score
    
    return quality

def capture_best_face():
    """Capture a single face from webcam with quality check"""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Cannot open webcam")
        return None
    
    print("\n" + "="*50)
    print("FACE REGISTRATION MODE")
    print("="*50)
    print("\nInstructions:")
    print("1. Position your face in the center of the frame")
    print("2. Make sure your face is well-lit")
    print("3. Look directly at the camera")
    print("4. Press 's' to save your face")
    print("5. Press 'q' to quit")
    print("\nWaiting for face detection...")
    
    best_face = None
    best_confidence = 0
    best_quality = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Resize for faster processing
        frame = cv2.resize(frame, (640, 480))
        
        # Detect faces
        results = yolo_model(frame, conf=0.5)
        
        # Find the best face in the frame
        current_best_face = None
        current_best_conf = 0
        current_best_quality = 0
        
        for r in results:
            for box in r.boxes:
                conf = float(box.conf[0])
                if conf > current_best_conf:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    
                    # Check face size
                    face_w = x2 - x1
                    face_h = y2 - y1
                    if face_w < 60 or face_h < 60:  # Increased minimum size
                        continue
                    
                    face_crop = frame[y1:y2, x1:x2]
                    
                    # Calculate quality
                    quality = calculate_face_quality(face_crop)
                    
                    # Combined score (confidence * quality)
                    combined_score = conf * quality
                    
                    if combined_score > current_best_conf:
                        current_best_face = face_crop
                        current_best_conf = conf
                        current_best_quality = quality
        
        # Update best face if current is better
        if current_best_face is not None and current_best_conf > best_confidence:
            best_face = current_best_face
            best_confidence = current_best_conf
            best_quality = current_best_quality
        
        # Draw face detection on frame
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                
                # Draw rectangle
                color = (0, 255, 0) if conf == best_confidence else (0, 0, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"Face {conf:.2f}", 
                           (x1, y1 - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 
                           0.6, color, 2)
        
        # Display instructions and quality info
        cv2.putText(frame, "Press 's' to save, 'q' to quit", 
                   (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                   0.7, (255, 255, 255), 2)
        cv2.putText(frame, f"Best confidence: {best_confidence:.2f}", 
                   (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 
                   0.7, (255, 255, 255), 2)
        
        if best_face is not None:
            cv2.putText(frame, f"Face quality: {best_quality:.2f}", 
                       (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 
                       0.7, (0, 255, 255), 2)
            
            # Show face preview
            preview_h = 80
            preview_w = int(best_face.shape[1] * preview_h / best_face.shape[0])
            # Ensure preview fits within frame bounds (max width 190 pixels from x=450)
            max_preview_w = 190
            if preview_w > max_preview_w:
                preview_w = max_preview_w
                preview_h = int(best_face.shape[0] * preview_w / best_face.shape[1])
            preview = cv2.resize(best_face, (preview_w, preview_h))
            frame[10:10+preview_h, 450:450+preview_w] = preview
        
        # Display frame
        cv2.imshow('Face Registration - Press s to save', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            if best_face is not None and best_confidence > 0.6 and best_quality > 0.5:
                print(f"\n✓ Face captured with confidence: {best_confidence:.2f}, quality: {best_quality:.2f}")
                break
            else:
                print("\n✗ No good face detected! Try again.")
                print(f"Current confidence: {best_confidence:.2f}, quality: {best_quality:.2f}")
        
        elif key == ord('q'):
            print("\n✗ Registration cancelled")
            best_face = None
            break
    
    cap.release()
    cv2.destroyAllWindows()
    
    if best_face is None:
        print("No face captured")
    
    return best_face

# -------------------------------
# Main registration function
# -------------------------------
def register_new_user():
    # Initialize database
    init_db()
    
    # Migrate old embeddings if needed
    migrate_old_embeddings()
    
    # Load existing embeddings
    user_ids, user_names, existing_embeddings, embedding_dims = load_embeddings()
    
    # Capture face
    print("\nCapturing face from webcam...")
    face_crop = capture_best_face()
    
    if face_crop is None:
        print("Face capture failed. Exiting.")
        return
    
    # Extract embedding using DeepFace
    print("\nExtracting face embedding with DeepFace...")
    embedding = extract_embedding_deepface(face_crop)
    
    if embedding is None:
        print("✗ Failed to extract embedding")
        return
    
    print(f"✓ Embedding extracted ({len(embedding)} dimensions)")
    print(f"  Mean: {np.mean(embedding):.6f}, Std: {np.std(embedding):.6f}")
    
    # Check if face already exists
    face_exists, match_idx, distance = check_if_face_exists(embedding, existing_embeddings, THRESHOLD)
    
    if face_exists:
        print(f"\n⚠️  WARNING: This face already exists in the database!")
        print(f"   Matched with: {user_names[match_idx]} (ID: {user_ids[match_idx]})")
        print(f"   Distance: {distance:.4f}")
        print(f"   Threshold: {THRESHOLD}")
        
        choice = input("\nDo you still want to register as a new user? (y/n): ").lower()
        if choice != 'y':
            print("Registration cancelled.")
            return
    else:
        if distance is not None:
            print(f"\n✓ New face detected (distance: {distance:.4f})")
        else:
            print(f"\n✓ New face detected")
    
    # Get user information
    print("\n" + "="*50)
    print("ENTER USER INFORMATION")
    print("="*50)
    
    name = input("\nEnter full name: ").strip()
    sr_code = input("Enter SR Code: ").strip()
    course = input("Enter course: ").strip()
    
    # Validate input
    if not name or not sr_code or not course:
        print("Error: All fields are required!")
        return
    
    # Check if SR code already exists
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM users WHERE sr_code = ?", (sr_code,))
    existing_user = c.fetchone()
    conn.close()
    
    if existing_user:
        print(f"\n⚠️  SR Code {sr_code} already registered to: {existing_user[0]}")
        choice = input("Do you want to continue anyway? (y/n): ").lower()
        if choice != 'y':
            print("Registration cancelled.")
            return
    
    # Confirm registration
    print("\n" + "="*50)
    print("REGISTRATION SUMMARY")
    print("="*50)
    print(f"Name: {name}")
    print(f"SR Code: {sr_code}")
    print(f"Course: {course}")
    print(f"Face quality: Good")
    print(f"Embedding dimensions: {len(embedding)}")
    
    confirm = input("\nConfirm registration? (y/n): ").lower()
    if confirm != 'y':
        print("Registration cancelled.")
        return
    
    # Save user data
    os.makedirs(BASE_SAVE_DIR, exist_ok=True)
    
    # Count existing users to determine next ID
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    user_count = c.fetchone()[0] + 1
    conn.close()
    
    # Create user folder
    user_folder = os.path.join(BASE_SAVE_DIR, f"user_{user_count}")
    os.makedirs(user_folder, exist_ok=True)
    
    # Save face image
    timestamp = int(time.time() * 1000)
    filename = os.path.join(user_folder, f"face_{timestamp}.jpg")
    cv2.imwrite(filename, face_crop)
    print(f"✓ Face image saved to: {filename}")
    
    # Save to database
    user_id = save_user(embedding, filename, name, sr_code, course)
    
    # Show success message
    print("\n" + "="*50)
    print("✅ REGISTRATION SUCCESSFUL!")
    print("="*50)
    print(f"User ID: {user_id}")
    print(f"Name: {name}")
    print(f"SR Code: {sr_code}")
    print(f"Course: {course}")
    print(f"Model: {FACE_MODEL}")
    print(f"Embedding dimensions: {len(embedding)}")
    print(f"\nYou can now use the main application.")
    print("="*50)
    
    # Show the captured face
    cv2.imshow('Captured Face', face_crop)
    cv2.waitKey(3000)  # Show for 3 seconds
    cv2.destroyAllWindows()

# -------------------------------
# List existing users
# -------------------------------
def list_existing_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if embedding_dim column exists
    try:
        c.execute("SELECT user_id, name, sr_code, course, embedding_dim FROM users ORDER BY user_id")
    except sqlite3.OperationalError:
        c.execute("SELECT user_id, name, sr_code, course FROM users ORDER BY user_id")
    
    users = c.fetchall()
    conn.close()
    
    print("\n" + "="*50)
    print("EXISTING USERS")
    print("="*50)
    
    if not users:
        print("No users registered yet.")
        return
    
    if len(users[0]) == 5:  # Has embedding_dim
        for user_id, name, sr_code, course, embedding_dim in users:
            print(f"ID: {user_id:3d} | Name: {name:20s} | SR Code: {sr_code:10s} | Course: {course:15s} | Dim: {embedding_dim}")
    else:
        for user_id, name, sr_code, course in users:
            print(f"ID: {user_id:3d} | Name: {name:20s} | SR Code: {sr_code:10s} | Course: {course}")

# -------------------------------
# Verify face recognition
# -------------------------------
def verify_recognition():
    """Test face recognition with existing database"""
    print("\n" + "="*50)
    print("FACE VERIFICATION TEST")
    print("="*50)
    
    # Load existing embeddings
    user_ids, user_names, existing_embeddings, embedding_dims = load_embeddings()
    
    if not existing_embeddings:
        print("No users in database. Please register users first.")
        return
    
    print(f"Database has embeddings with dimensions: {set(embedding_dims)}")
    
    # Capture a face to test
    print("\nCapturing face for verification...")
    face_crop = capture_best_face()
    
    if face_crop is None:
        print("Face capture failed.")
        return
    
    # Extract embedding
    print("\nExtracting embedding...")
    embedding = extract_embedding_deepface(face_crop)
    
    if embedding is None:
        print("Failed to extract embedding.")
        return
    
    # Check against database
    face_exists, match_idx, distance = check_if_face_exists(embedding, existing_embeddings, THRESHOLD)
    
    print("\n" + "="*50)
    print("VERIFICATION RESULT")
    print("="*50)
    
    if face_exists:
        print(f"✅ MATCH FOUND!")
        print(f"   Name: {user_names[match_idx]}")
        print(f"   ID: {user_ids[match_idx]}")
        print(f"   Distance: {distance:.4f}")
        print(f"   Threshold: {THRESHOLD}")
    else:
        print(f"❌ NO MATCH FOUND")
        if match_idx != -1:
            print(f"   Closest match: {user_names[match_idx]} (distance: {distance:.4f})")
        print(f"   Threshold: {THRESHOLD}")
    
    # Show the test face
    cv2.imshow('Test Face', face_crop)
    cv2.waitKey(3000)
    cv2.destroyAllWindows()

# -------------------------------
# Delete user function
# -------------------------------
def delete_user():
    list_existing_users()
    
    try:
        user_id = int(input("\nEnter User ID to delete (0 to cancel): "))
        if user_id == 0:
            return
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get user info before deleting
        c.execute("SELECT name, image_path FROM users WHERE user_id=?", (user_id,))
        user_info = c.fetchone()
        
        if not user_info:
            print(f"User ID {user_id} not found.")
            return
        
        name, image_path = user_info
        
        # Confirm deletion
        print(f"\nYou are about to delete:")
        print(f"  Name: {name}")
        print(f"  ID: {user_id}")
        
        confirm = input("\nAre you sure? This cannot be undone. (y/n): ").lower()
        if confirm != 'y':
            print("Deletion cancelled.")
            return
        
        # Delete from database
        c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        
        # Try to delete the image file
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
                # Try to remove the user folder if empty
                user_folder = os.path.dirname(image_path)
                if os.path.exists(user_folder) and not os.listdir(user_folder):
                    os.rmdir(user_folder)
            except:
                print(f"Note: Could not delete image file: {image_path}")
        
        conn.commit()
        conn.close()
        
        print(f"\n✅ User '{name}' (ID: {user_id}) deleted successfully.")
        
    except ValueError:
        print("Invalid input. Please enter a valid User ID.")
    except Exception as e:
        print(f"Error deleting user: {e}")

# -------------------------------
# Clean database function
# -------------------------------
def clean_database():
    """Remove users with incompatible embedding dimensions"""
    print("\n" + "="*50)
    print("CLEAN DATABASE")
    print("="*50)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all users and their embedding dimensions
    c.execute("SELECT user_id, name, embedding, embedding_dim FROM users")
    users = c.fetchall()
    
    incompatible_users = []
    
    for user_id, name, emb_blob, stored_dim in users:
        if emb_blob:
            actual_dim = len(np.frombuffer(emb_blob, dtype=np.float32))
            if actual_dim != 128:  # DeepFace Facenet uses 128 dimensions
                incompatible_users.append((user_id, name, actual_dim, stored_dim))
    
    if not incompatible_users:
        print("All users have compatible 128-dim embeddings.")
        conn.close()
        return
    
    print(f"Found {len(incompatible_users)} users with incompatible embeddings:")
    for user_id, name, actual_dim, stored_dim in incompatible_users:
        print(f"  ID {user_id}: {name} - {actual_dim} dimensions (stored as {stored_dim})")
    
    choice = input("\nDo you want to delete these users? (y/n): ").lower()
    if choice == 'y':
        for user_id, name, actual_dim, stored_dim in incompatible_users:
            # Get image path before deleting
            c.execute("SELECT image_path FROM users WHERE user_id=?", (user_id,))
            result = c.fetchone()
            image_path = result[0] if result else None
            
            # Delete from database
            c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            
            # Try to delete image file
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    user_folder = os.path.dirname(image_path)
                    if os.path.exists(user_folder) and not os.listdir(user_folder):
                        os.rmdir(user_folder)
                except:
                    pass
            
            print(f"  Deleted: {name} (ID: {user_id})")
        
        conn.commit()
        print(f"\n✅ Deleted {len(incompatible_users)} incompatible users.")
    else:
        print("No users deleted.")
    
    conn.close()

# -------------------------------
# Main menu
# -------------------------------
def main_menu():
    while True:
        print("\n" + "="*50)
        print("FACE REGISTRATION SYSTEM WITH DEEPFACE")
        print("="*50)
        print(f"Model: {FACE_MODEL}")
        print(f"Threshold: {THRESHOLD}")
        print("="*50)
        print("\n1. Register New User")
        print("2. List Existing Users")
        print("3. Verify Face Recognition")
        print("4. Delete User")
        print("5. Clean Database (remove incompatible embeddings)")
        print("6. Exit")
        print("="*50)
        
        choice = input("\nEnter your choice (1-6): ").strip()
        
        if choice == '1':
            register_new_user()
        elif choice == '2':
            list_existing_users()
        elif choice == '3':
            verify_recognition()
        elif choice == '4':
            delete_user()
        elif choice == '5':
            clean_database()
        elif choice == '6':
            print("\nExiting registration system. Goodbye!")
            break
        else:
            print("Invalid choice. Please enter 1, 2, 3, 4, 5, or 6.")

# -------------------------------
# Run the program
# -------------------------------
if __name__ == "__main__":
    print("Face Registration System with DeepFace")
    print("="*50)
    print("This tool allows you to register new users separately")
    print("from the main facial recognition application.")
    print(f"Using model: {FACE_MODEL}")
    print("="*50)
    
    main_menu()