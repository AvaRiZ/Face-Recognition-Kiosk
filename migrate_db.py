import sqlite3
import pickle
import numpy as np
import cv2
import os
from deepface import DeepFace
from pathlib import Path

DB_PATH = "database/faces_improved.db"
BASE_SAVE_DIR = "faces_improved"

PRIMARY_MODEL = "ArcFace"
SECONDARY_MODEL = "Facenet"

def extract_dual_embeddings(face_path):
    """Extract BOTH model embeddings from face image"""
    try:
        face_img = cv2.imread(face_path)
        if face_img is None:
            return None
        
        face_rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
        embeddings = []
        
        for model_name in [PRIMARY_MODEL, SECONDARY_MODEL]:
            embedding_obj = DeepFace.represent(
                img_path=face_rgb,
                model_name=model_name,
                enforce_detection=False,
                detector_backend='skip',
                align=True
            )
            embedding = np.array(embedding_obj[0]['embedding'], dtype=np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding /= norm
            embeddings.append(embedding)
            print(f"  ✓ {model_name}: {embedding.shape}")
        
        return embeddings  # List of 2 embeddings
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return None

def migrate_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT user_id, name, sr_code, image_paths FROM users")
    users = c.fetchall()
    
    print(f"Migrating {len(users)} users...")
    
    migrated = 0
    for user_id, name, sr_code, paths_str in users:
        paths = paths_str.split(';') if paths_str else []
        print(f"\nUser {user_id}: {name} ({sr_code}) - {len(paths)} images")
        
        new_embeddings_all = []
        for img_path in paths[:10]:  # First 10 images
            if os.path.exists(img_path):
                embeddings = extract_dual_embeddings(img_path)
                if embeddings:
                    new_embeddings_all.extend(embeddings)
        
        if new_embeddings_all:
            embeddings_blob = pickle.dumps(new_embeddings_all)
            c.execute("""
                UPDATE users 
                SET embeddings = ?, embedding_dim = ?, last_updated = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (embeddings_blob, len(new_embeddings_all[0]), user_id))
            migrated += 1
            print(f"  ✓ Migrated {len(new_embeddings_all)} embeddings")
        else:
            print(f"  ✗ No valid embeddings")
    
    conn.commit()
    conn.close()
    print(f"\n✅ Migration complete: {migrated}/{len(users)} users updated")

if __name__ == "__main__":
    migrate_database()

