from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from core.config import AppConfig
from core.state import AppStateManager
from database.schema import init_canonical_schema
from db import connect as db_connect
from routes.ml_analytics import run_ml_analytics

try:
    from routes.internal_routes import create_internal_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_internal_blueprint = None

try:
    from routes.routes import create_routes_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_routes_blueprint = None


class _DummyRepository:
    def get_user_by_sr_code(self, _sr_code):
        return None

    def save_user(self, _user):
        return 1

    def get_user_by_id(self, _user_id):
        return None

    def update_embeddings(self, _user_id, _new_embeddings, image_path=None):
        return None

    def get_all_users(self):
        return []


class AnalyticsPipelineResilienceTests(unittest.TestCase):
    def test_run_ml_analytics_reads_canonical_events_without_legacy_log_table(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "analytics.db")

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                sr_code TEXT UNIQUE,
                course TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE recognition_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                station_id TEXT,
                user_id INTEGER,
                sr_code TEXT,
                decision TEXT NOT NULL,
                confidence REAL,
                captured_at TEXT,
                ingested_at TEXT
            )
            """
        )
        c.execute(
            """
            INSERT INTO users (name, sr_code, course)
            VALUES ('Alice', 'SR001', 'BSCS')
            """
        )
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, station_id, user_id, sr_code, decision, confidence, captured_at, ingested_at
            )
            VALUES (
                'evt-001', 'entrance-station-1', 1, 'SR001', 'allowed', 0.93, '2026-04-22 08:30:00',
                '2026-04-22 08:30:01'
            )
            """
        )
        conn.commit()
        conn.close()

        stub_forecast = {
            "primary_forecast": {},
            "all_forecasts": [],
            "comparison": [],
            "best_model": "",
            "errors": {},
            "warnings": {},
            "successful_models": 0,
            "attempted_models": 0,
            "comparison_interpretation": "",
        }
        with patch("routes.ml_analytics.run_all_forecasts", return_value=stub_forecast):
            result = run_ml_analytics(db_path)

        self.assertNotIn("message", result)
        self.assertEqual(result.get("data_quality", {}).get("total_live"), 1)

    def test_run_ml_analytics_accepts_datetime_live_timestamps(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "analytics_datetime.db")

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                sr_code TEXT UNIQUE,
                course TEXT
            )
            """
        )
        conn.commit()
        conn.close()

        live_rows = [
            ("SR001", "Alice", "BSCS", None, None, 0.93, datetime(2026, 4, 22, 13, 20, 48), "live")
        ]
        stub_forecast = {
            "primary_forecast": {},
            "all_forecasts": [],
            "comparison": [],
            "best_model": "",
            "errors": {},
            "warnings": {},
            "successful_models": 0,
            "attempted_models": 0,
            "comparison_interpretation": "",
        }
        with patch("routes.ml_analytics._fetch_live_rows", return_value=live_rows):
            with patch("routes.ml_analytics.run_all_forecasts", return_value=stub_forecast):
                result = run_ml_analytics(db_path)

        self.assertNotIn("message", result)
        self.assertEqual(result.get("data_quality", {}).get("total_live"), 1)


@unittest.skipIf(create_internal_blueprint is None, "Internal route blueprint dependencies are unavailable.")
class InternalIngestRealtimeTests(unittest.TestCase):
    def test_internal_ingest_emits_realtime_update_even_without_legacy_log_table(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "internal.db")
        init_canonical_schema(db_path)

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sr_code TEXT UNIQUE
            )
            """
        )
        c.execute("INSERT INTO users (sr_code) VALUES ('SR001')")
        conn.commit()
        conn.close()

        config = AppConfig()
        config.db_path = db_path
        state = AppStateManager(config)

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "db_path": db_path,
            "repository": _DummyRepository(),
            "get_thresholds": state.get_thresholds,
            "config": config,
        }
        app.register_blueprint(create_internal_blueprint(deps))
        client = app.test_client()

        with patch("routes.internal_routes.emit_analytics_update") as emit_mock:
            response = client.post(
                "/api/internal/recognition-events",
                json={
                    "event_id": "evt-ingest-1",
                    "sr_code": "SR001",
                    "decision": "allowed",
                    "confidence": 0.88,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload.get("duplicate"), False)
        emit_mock.assert_called_once()
        self.assertEqual(emit_mock.call_args[0][0], "recognition_event_ingested")

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM recognition_events")
        self.assertEqual(c.fetchone()[0], 1)
        conn.close()


@unittest.skipIf(create_routes_blueprint is None, "Route blueprint dependencies are unavailable.")
class ApiEventsFallbackTests(unittest.TestCase):
    def test_api_events_falls_back_to_legacy_recognition_log(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)
        db_path = str(temp_path / "events.db")

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                sr_code TEXT UNIQUE
            )
            """
        )
        c.execute(
            """
            CREATE TABLE recognition_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                confidence REAL,
                timestamp TEXT
            )
            """
        )
        c.execute("INSERT INTO users (name, sr_code) VALUES ('Alice', 'SR001')")
        c.execute(
            """
            INSERT INTO recognition_log (user_id, confidence, timestamp)
            VALUES (1, 0.91, '2026-04-22 08:45:00')
            """
        )
        conn.commit()
        conn.close()

        config = AppConfig()
        config.db_path = db_path
        config.base_save_dir = str(temp_path / "faces")
        state = AppStateManager(config)

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "config": config,
            "db_path": db_path,
            "base_save_dir": config.base_save_dir,
            "repository": _DummyRepository(),
            "worker_runtime_attached": True,
            "get_thresholds": state.get_thresholds,
            "set_thresholds": state.set_thresholds,
            "get_user_count": lambda: state.user_count,
            "get_registration_state": lambda: state.registration_state,
            "capture_registration_sample": state.capture_registration_sample,
            "get_current_registration_pose": state.get_current_registration_pose,
            "get_registration_progress": state.get_registration_progress,
            "is_registration_ready": state.is_registration_ready,
            "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
            "reset_database_state": state.reset_database_state,
            "reset_registration_state": state.reset_registration_state,
            "start_web_registration_session": state.start_web_registration_session,
            "cancel_web_registration_session": state.cancel_web_registration_session,
            "set_registration_status_reason": state.set_registration_status_reason,
            "clear_registration_status_reason": state.clear_registration_status_reason,
            "complete_registration": state.complete_registration,
            "remove_user_embedding": state.remove_user,
            "replace_user": state.replace_user,
            "render_markdown_as_html": lambda _path: "",
            "pause_detection": lambda: None,
            "resume_detection": lambda: None,
            "detection_paused": lambda: False,
            "stream_status": lambda: {"state": "live", "message": "Camera stream active."},
            "yolo_model": None,
            "yolo_device": "cpu",
        }
        app.register_blueprint(create_routes_blueprint(deps))
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["staff_id"] = 1
            sess["username"] = "tester"
            sess["role"] = "library_staff"

        response = client.get("/api/events")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        rows = payload.get("rows", [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("sr_code"), "SR001")

    def test_api_events_accepts_datetime_timestamp_values(self) -> None:
        class _FakeCursor:
            def execute(self, _query, _params=None):
                return None

            def fetchall(self):
                return [("Alice", "SR001", 0.91, datetime(2026, 4, 22, 13, 20, 48))]

        class _FakeConn:
            def __init__(self):
                self._cursor = _FakeCursor()

            def cursor(self):
                return self._cursor

            def close(self):
                return None

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)
        db_path = str(temp_path / "events_datetime.db")

        config = AppConfig()
        config.db_path = db_path
        config.base_save_dir = str(temp_path / "faces")
        state = AppStateManager(config)

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "config": config,
            "db_path": db_path,
            "base_save_dir": config.base_save_dir,
            "repository": _DummyRepository(),
            "worker_runtime_attached": True,
            "get_thresholds": state.get_thresholds,
            "set_thresholds": state.set_thresholds,
            "get_user_count": lambda: state.user_count,
            "get_registration_state": lambda: state.registration_state,
            "capture_registration_sample": state.capture_registration_sample,
            "get_current_registration_pose": state.get_current_registration_pose,
            "get_registration_progress": state.get_registration_progress,
            "is_registration_ready": state.is_registration_ready,
            "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
            "reset_database_state": state.reset_database_state,
            "reset_registration_state": state.reset_registration_state,
            "start_web_registration_session": state.start_web_registration_session,
            "cancel_web_registration_session": state.cancel_web_registration_session,
            "set_registration_status_reason": state.set_registration_status_reason,
            "clear_registration_status_reason": state.clear_registration_status_reason,
            "complete_registration": state.complete_registration,
            "remove_user_embedding": state.remove_user,
            "replace_user": state.replace_user,
            "render_markdown_as_html": lambda _path: "",
            "pause_detection": lambda: None,
            "resume_detection": lambda: None,
            "detection_paused": lambda: False,
            "stream_status": lambda: {"state": "live", "message": "Camera stream active."},
            "yolo_model": None,
            "yolo_device": "cpu",
        }
        app.register_blueprint(create_routes_blueprint(deps))
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["staff_id"] = 1
            sess["username"] = "tester"
            sess["role"] = "library_staff"

        with patch("routes.routes.db_connect", return_value=_FakeConn()):
            response = client.get("/api/events")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        rows = payload.get("rows", [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("date"), "2026-04-22")
        self.assertEqual(rows[0].get("timestamp"), "2026-04-22 13:20:48")


if __name__ == "__main__":
    unittest.main()
