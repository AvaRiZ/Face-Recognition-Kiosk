# test_deepface.py
import cv2
import numpy as np
import os
from deepface import DeepFace

print("Testing DeepFace...")

# Create test faces
print("\nCreating test faces...")
test_dir = "test_deepface_faces"
os.makedirs(test_dir, exist_ok=True)

# Create different test patterns
faces = []

# Face 1: Top-bottom
face1 = np.zeros((160, 160, 3), dtype=np.uint8)
face1[:80, :] = [255, 255, 255]
faces.append(("top_bottom", face1))
cv2.imwrite(f"{test_dir}/face1_top_bottom.jpg", face1)

# Face 2: Left-right
face2 = np.zeros((160, 160, 3), dtype=np.uint8)
face2[:, :80] = [255, 255, 255]
faces.append(("left_right", face2))
cv2.imwrite(f"{test_dir}/face2_left_right.jpg", face2)

# Face 3: Gradient
face3 = np.zeros((160, 160, 3), dtype=np.uint8)
for i in range(160):
    intensity = int(255 * i / 160)
    face3[i, :] = [intensity, intensity, intensity]
faces.append(("gradient", face3))
cv2.imwrite(f"{test_dir}/face3_gradient.jpg", face3)

print(f"✓ Created {len(faces)} test faces in '{test_dir}'")

# Test DeepFace
print("\n" + "="*60)
print("TESTING DEEPFACE")
print("="*60)

embeddings = []

for name, face_img in faces:
    print(f"\nProcessing {name}...")
    
    try:
        # Convert BGR to RGB (DeepFace expects RGB)
        face_rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
        
        # Get embedding using Facenet (it will download model automatically)
        embedding_obj = DeepFace.represent(
            face_rgb, 
            model_name='Facenet',  # Or 'VGG-Face', 'OpenFace', 'DeepID', etc.
            enforce_detection=False,  # Don't try to detect faces in our patterns
            detector_backend='skip'   # Skip face detection
        )
        
        # Extract embedding
        embedding = embedding_obj[0]['embedding']
        embedding = np.array(embedding, dtype=np.float32)
        
        print(f"  ✓ Embedding extracted")
        print(f"    Dimensions: {len(embedding)}")
        print(f"    Mean: {np.mean(embedding):.6f}")
        print(f"    Std: {np.std(embedding):.6f}")
        
        embeddings.append((name, embedding))
        
    except Exception as e:
        print(f"  ✗ Error: {e}")

# Compare embeddings
print("\n" + "="*60)
print("COMPARING EMBEDDINGS")
print("="*60)

if len(embeddings) >= 2:
    for i in range(len(embeddings)):
        for j in range(i+1, len(embeddings)):
            name1, emb1 = embeddings[i]
            name2, emb2 = embeddings[j]
            
            # Calculate distances
            euclidean = np.linalg.norm(emb1 - emb2)
            
            # Cosine similarity
            dot = np.dot(emb1, emb2)
            norm1 = np.linalg.norm(emb1)
            norm2 = np.linalg.norm(emb2)
            
            if norm1 > 0 and norm2 > 0:
                cosine_sim = dot / (norm1 * norm2)
                cosine_dist = 1 - cosine_sim
            else:
                cosine_dist = 1.0
            
            print(f"\n{name1} vs {name2}:")
            print(f"  Euclidean distance: {euclidean:.6f}")
            print(f"  Cosine distance: {cosine_dist:.6f}")
            
            if euclidean < 0.1:
                print(f"  ⚠️  WARNING: Faces are very similar!")
            else:
                print(f"  ✓ Good: Faces are different")
else:
    print("Not enough embeddings to compare")

print("\n" + "="*60)
print("TEST COMPLETE")
print("="*60)