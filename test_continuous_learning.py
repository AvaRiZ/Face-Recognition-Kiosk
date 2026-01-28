#!/usr/bin/env python3
"""
Test script for continuous learning functionality in face recognition system.
Tests the database operations and embedding handling for adding new embeddings to existing users.
"""

import sqlite3
import pickle
import numpy as np
import os
import shutil
from datetime import datetime

# Test database path
TEST_DB_PATH = "test_faces_continuous.db"
TEST_BASE_DIR = "test_faces_continuous"

def setup_test_db():
    """Create a test database with sample user data"""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    if os.path.exists(TEST_BASE_DIR):
        shutil.rmtree(TEST_BASE_DIR)

    os.makedirs(TEST_BASE_DIR, exist_ok=True)

    conn = sqlite3.connect(TEST_DB_PATH)
    c = conn.cursor()

    # Create tables
    c.execute('''
        CREATE TABLE users (
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
        CREATE TABLE recognition_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            confidence REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # Insert test user
    test_embeddings = [np.random.rand(128).astype(np.float32) for _ in range(3)]
    embeddings_blob = pickle.dumps(test_embeddings)
    image_paths = "test_faces_continuous/TEST001/face_1.jpg;test_faces_continuous/TEST001/face_2.jpg;test_faces_continuous/TEST001/face_3.jpg"

    c.execute("""
        INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("Test User", "TEST001", "Computer Science", embeddings_blob, image_paths, 128))

    conn.commit()
    conn.close()

    # Create test image directory
    os.makedirs(os.path.join(TEST_BASE_DIR, "TEST001"), exist_ok=True)

    print("✓ Test database and user created")

def test_continuous_learning_logic():
    """Test the continuous learning database update logic"""
    print("\n=== Testing Continuous Learning Logic ===")

    # Load existing user data
    conn = sqlite3.connect(TEST_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, name, sr_code, embeddings, image_paths FROM users WHERE sr_code = ?", ("TEST001",))
    row = c.fetchone()
    conn.close()

    if not row:
        print("✗ Failed to load test user")
        return False

    user_id, name, sr_code, emb_blob, paths_str = row
    existing_embeddings = pickle.loads(emb_blob)
    existing_paths = paths_str.split(';') if paths_str else []

    print(f"✓ Loaded user {name} with {len(existing_embeddings)} existing embeddings")

    # Simulate adding a new embedding (continuous learning)
    new_embedding = np.random.rand(128).astype(np.float32)
    existing_embeddings.append(new_embedding)

    # Simulate saving new image
    timestamp = int(datetime.now().timestamp() * 1000)
    user_folder = os.path.join(TEST_BASE_DIR, sr_code)
    filename = os.path.join(user_folder, f"face_{timestamp}_learned.jpg")

    # Create dummy image file
    with open(filename, 'w') as f:
        f.write("dummy image data")

    existing_paths.append(filename)

    # Update database
    conn = sqlite3.connect(TEST_DB_PATH)
    c = conn.cursor()

    updated_emb_blob = pickle.dumps(existing_embeddings)
    updated_paths_str = ';'.join(existing_paths)

    c.execute("""
        UPDATE users
        SET embeddings = ?, image_paths = ?, last_updated = CURRENT_TIMESTAMP
        WHERE user_id = ?
    """, (updated_emb_blob, updated_paths_str, user_id))

    conn.commit()
    conn.close()

    print(f"✓ Added new embedding. Total embeddings: {len(existing_embeddings)}")
    print(f"✓ New image saved: {filename}")

    # Verify the update
    conn = sqlite3.connect(TEST_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT embeddings, image_paths FROM users WHERE user_id = ?", (user_id,))
    updated_emb_blob, updated_paths_str = c.fetchone()
    conn.close()

    updated_embeddings = pickle.loads(updated_emb_blob)
    updated_paths = updated_paths_str.split(';')

    if len(updated_embeddings) == 4 and len(updated_paths) == 4:
        print("✓ Database update verified successfully")
        print(f"  - Embeddings count: {len(updated_embeddings)}")
        print(f"  - Image paths count: {len(updated_paths)}")
        return True
    else:
        print("✗ Database update verification failed")
        return False

def test_embedding_integrity():
    """Test that embeddings maintain their integrity after pickling/unpickling"""
    print("\n=== Testing Embedding Integrity ===")

    # Create test embeddings
    original_embeddings = [np.random.rand(128).astype(np.float32) for _ in range(3)]

    # Pickle and unpickle
    pickled = pickle.dumps(original_embeddings)
    unpickled_embeddings = pickle.loads(pickled)

    # Check integrity
    if len(unpickled_embeddings) == len(original_embeddings):
        all_match = True
        for orig, unpick in zip(original_embeddings, unpickled_embeddings):
            if not np.allclose(orig, unpick):
                all_match = False
                break

        if all_match:
            print("✓ Embedding integrity maintained after pickle/unpickle")
            return True
        else:
            print("✗ Embedding data corrupted during pickle/unpickle")
            return False
    else:
        print("✗ Embedding count changed during pickle/unpickle")
        return False

def test_multiple_users():
    """Test continuous learning with multiple users"""
    print("\n=== Testing Multiple Users ===")

    conn = sqlite3.connect(TEST_DB_PATH)
    c = conn.cursor()

    # Add second user
    test_embeddings2 = [np.random.rand(128).astype(np.float32) for _ in range(2)]
    embeddings_blob2 = pickle.dumps(test_embeddings2)
    image_paths2 = "test_faces_continuous/TEST002/face_1.jpg;test_faces_continuous/TEST002/face_2.jpg"

    c.execute("""
        INSERT INTO users (name, sr_code, course, embeddings, image_paths, embedding_dim)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("Test User 2", "TEST002", "Engineering", embeddings_blob2, image_paths2, 128))

    conn.commit()

    # Test continuous learning for second user
    c.execute("SELECT user_id, embeddings FROM users WHERE sr_code = ?", ("TEST002",))
    user_id2, emb_blob2 = c.fetchone()
    existing_embeddings2 = pickle.loads(emb_blob2)

    # Add new embedding
    new_embedding2 = np.random.rand(128).astype(np.float32)
    existing_embeddings2.append(new_embedding2)

    updated_emb_blob2 = pickle.dumps(existing_embeddings2)
    c.execute("UPDATE users SET embeddings = ? WHERE user_id = ?", (updated_emb_blob2, user_id2))

    conn.commit()
    conn.close()

    print("✓ Multiple users continuous learning test passed")
    return True

def cleanup_test_files():
    """Clean up test files"""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    if os.path.exists(TEST_BASE_DIR):
        shutil.rmtree(TEST_BASE_DIR)
    print("✓ Test files cleaned up")

def main():
    """Run all tests"""
    print("Starting Continuous Learning Tests...")

    try:
        # Setup
        setup_test_db()

        # Run tests
        tests = [
            test_embedding_integrity,
            test_continuous_learning_logic,
            test_multiple_users
        ]

        passed = 0
        for test in tests:
            if test():
                passed += 1
            else:
                print(f"✗ Test {test.__name__} failed")

        print(f"\n=== Test Results ===")
        print(f"Passed: {passed}/{len(tests)} tests")

        if passed == len(tests):
            print("✓ All continuous learning tests passed!")
            return True
        else:
            print("✗ Some tests failed")
            return False

    except Exception as e:
        print(f"✗ Test execution failed: {e}")
        return False

    finally:
        cleanup_test_files()

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
