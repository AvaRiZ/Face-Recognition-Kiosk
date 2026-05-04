from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

import auth
from core.models import RecognitionResult, User

if "app.realtime" not in sys.modules:
    realtime_stub = types.ModuleType("app.realtime")
    realtime_stub.emit_analytics_update = lambda *_args, **_kwargs: None
    realtime_stub.emit_capacity_threshold_alert = lambda *_args, **_kwargs: None
    realtime_stub.emit_unrecognized_detection = lambda *_args, **_kwargs: None
    sys.modules["app.realtime"] = realtime_stub

from database.repository import UserRepository
from database.schema import init_canonical_schema
from db import connect as db_connect


class AuthBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "auth.db")
        self.original_db_path = auth.DB_PATH
        auth.DB_PATH = self.db_path
        self.addCleanup(self._restore_auth_db_path)
        self._saved_env = {
            "ALLOW_DEFAULT_ADMIN_BOOTSTRAP": os.environ.get("ALLOW_DEFAULT_ADMIN_BOOTSTRAP"),
            "APP_ENV": os.environ.get("APP_ENV"),
            "FLASK_ENV": os.environ.get("FLASK_ENV"),
        }
        self.addCleanup(self._restore_env)

    def _restore_auth_db_path(self) -> None:
        auth.DB_PATH = self.original_db_path

    def _restore_env(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_init_auth_db_does_not_seed_default_admin_when_bootstrap_disabled(self) -> None:
        os.environ.pop("ALLOW_DEFAULT_ADMIN_BOOTSTRAP", None)
        os.environ.pop("APP_ENV", None)
        os.environ.pop("FLASK_ENV", None)

        auth.init_auth_db()

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM staff_accounts")
        self.assertEqual(c.fetchone()[0], 0)
        conn.close()

    def test_init_auth_db_seeds_default_admin_when_bootstrap_enabled(self) -> None:
        os.environ["ALLOW_DEFAULT_ADMIN_BOOTSTRAP"] = "1"

        auth.init_auth_db()

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT username FROM staff_accounts")
        rows = c.fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "admin")


class RepositoryCanonicalizationTests(unittest.TestCase):
    def test_repository_writes_canonical_events_and_normalized_embeddings(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "repo.db")

        repo = UserRepository(db_path)
        repo.init_db()
        init_canonical_schema(db_path)

        user = User(
            id=0,
            name="Alice",
            sr_code="SR001",
            gender="Female",
            program="BSCS",
            embeddings={
                "ArcFace": [np.array([0.1, 0.2, 0.3], dtype=np.float32)],
                "Facenet": [np.array([0.4, 0.5, 0.6], dtype=np.float32)],
            },
            image_paths=["faces/alice_1.jpg"],
            embedding_dim=3,
        )
        user_id = repo.save_user(user)

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM user_embeddings WHERE user_id = %s", (user_id,))
        embedding_rows = int(c.fetchone()[0] or 0)
        c.execute("SELECT embeddings FROM users WHERE user_id = %s", (user_id,))
        legacy_blob = c.fetchone()[0]
        conn.close()

        self.assertEqual(embedding_rows, 2)
        if isinstance(legacy_blob, memoryview):
            legacy_blob = legacy_blob.tobytes()
        if isinstance(legacy_blob, bytearray):
            legacy_blob = bytes(legacy_blob)
        self.assertEqual(legacy_blob or b"", b"")
        self.assertEqual(repo.count_user_embeddings(user_id), 2)

        stored_user = repo.get_user_by_id(user_id)
        self.assertIsNotNone(stored_user)
        self.assertIn("ArcFace", stored_user.embeddings)
        self.assertIn("Facenet", stored_user.embeddings)

        result = RecognitionResult(
            user_id=user_id,
            confidence=0.95,
            primary_confidence=0.96,
            secondary_confidence=0.94,
            distance=0.05,
            primary_distance=0.04,
            secondary_distance=0.06,
            threshold=0.8,
            user=stored_user,
            user_index=0,
        )
        repo.log_recognition(result, face_quality=0.92, method="two-factor")

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM recognition_events")
        event_count = int(c.fetchone()[0] or 0)
        c.execute("SELECT decision FROM recognition_events LIMIT 1")
        decision = c.fetchone()[0]
        conn.close()

        self.assertEqual(event_count, 1)
        self.assertEqual(decision, "allowed")


if __name__ == "__main__":
    unittest.main()
