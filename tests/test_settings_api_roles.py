from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session

from core.config import AppConfig
from core.state import AppStateManager

if "flask_socketio" not in sys.modules:
    class _SocketIoStub:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            return None

    sys.modules["flask_socketio"] = types.SimpleNamespace(SocketIO=_SocketIoStub)

try:
    from routes.routes import create_routes_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_routes_blueprint = None


def _normalize_sql(query: str) -> str:
    return " ".join(str(query).split()).lower()


class _FakeSettingsCursor:
    def __init__(self, store: dict):
        self.store = store
        self._rows = []
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = _normalize_sql(query)
        values = list(params or [])
        self._rows = []
        self.rowcount = 0

        if normalized.startswith("select value from app_settings where key = %s"):
            key = str(values[0])
            value = self.store["app_settings"].get(key)
            self._rows = [(value,)] if value is not None else []
            return

        if normalized.startswith("insert into app_settings (key, value)"):
            key = str(values[0])
            value = str(values[1])
            self.store["app_settings"][key] = value
            self.rowcount = 1
            return

        if normalized.startswith("select audit_id, staff_id, username, action, target, ip_address, timestamp from audit_log"):
            action = str(values[0])
            limit = int(values[1])
            rows = [row for row in self.store["audit_log"] if row["action"] == action]
            rows.sort(key=lambda row: (row["timestamp"], row["audit_id"]), reverse=True)
            rows = rows[:limit]
            self._rows = [
                (
                    row["audit_id"],
                    row["staff_id"],
                    row["username"],
                    row["action"],
                    row["target"],
                    row["ip_address"],
                    row["timestamp"],
                )
                for row in rows
            ]
            return

        if normalized.startswith("select u.user_id, u.name, re.confidence from users u left join recognition_events re on u.user_id = re.user_id"):
            self._rows = list(self.store["stats_rows"])
            return

        if normalized.startswith("delete from recognition_events"):
            self.store["stats_rows"] = []
            self.rowcount = 1
            return

        if normalized.startswith("delete from user_embeddings"):
            self.store["user_embeddings"] = []
            self.rowcount = 1
            return

        if normalized.startswith("delete from users"):
            self.store["users"] = []
            self.rowcount = 1
            return

        raise AssertionError(f"Unexpected SQL in test double: {normalized}")

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


class _FakeSettingsConnection:
    def __init__(self, store: dict):
        self.store = store

    def cursor(self):
        return _FakeSettingsCursor(self.store)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


@unittest.skipIf(create_routes_blueprint is None, "Route blueprint dependencies are unavailable.")
class SettingsApiRoleTests(unittest.TestCase):
    def _build_client(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)

        config = AppConfig()
        config.db_path = str(temp_path / "settings.db")
        config.base_save_dir = str(temp_path / "faces")
        Path(config.base_save_dir).mkdir(parents=True, exist_ok=True)
        (Path(config.base_save_dir) / "placeholder.txt").write_text("sample", encoding="utf-8")

        state = AppStateManager(config)
        self.store = {
            "app_settings": {
                "threshold": "0.3",
                "quality_threshold": "0.2",
                "vector_index_top_k": "20",
                "max_occupancy": "300",
            },
            "audit_log": [],
            "stats_rows": [
                (1, "Alice Cruz", 0.91),
                (1, "Alice Cruz", 0.87),
                (2, "Brian Gomez", 0.79),
            ],
            "user_embeddings": [101, 102],
            "users": [1, 2, 3],
            "audit_counter": 0,
        }

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "config": config,
            "db_path": config.db_path,
            "base_save_dir": config.base_save_dir,
            "get_thresholds": state.get_thresholds,
            "set_thresholds": state.set_thresholds,
            "get_user_count": lambda: len(self.store["users"]),
            "reset_database_state": state.reset_database_state,
            "reset_registration_state": state.reset_registration_state,
            "get_registration_state": lambda: state.registration_state,
            "capture_registration_sample": state.capture_registration_sample,
            "get_current_registration_pose": state.get_current_registration_pose,
            "get_registration_progress": state.get_registration_progress,
            "is_registration_ready": state.is_registration_ready,
            "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
            "start_web_registration_session": state.start_web_registration_session,
            "cancel_web_registration_session": state.cancel_web_registration_session,
            "set_registration_status_reason": state.set_registration_status_reason,
            "clear_registration_status_reason": state.clear_registration_status_reason,
            "complete_registration": state.complete_registration,
            "remove_user_embedding": lambda _user_id: None,
            "replace_user": state.replace_user,
            "render_markdown_as_html": lambda _path: "",
            "pause_detection": lambda: None,
            "resume_detection": lambda: None,
            "detection_paused": lambda: False,
            "stream_status": lambda: {"state": "live", "message": "Camera stream active."},
            "repository": None,
            "worker_runtime_attached": True,
            "yolo_model": None,
            "yolo_device": "cpu",
        }

        with patch("routes.routes.init_imported_logs_table", return_value=None), patch(
            "routes.routes.ensure_version_settings", return_value=None
        ):
            app.register_blueprint(create_routes_blueprint(deps))
        return app.test_client()

    def _db_connect_stub(self, _db_path):
        return _FakeSettingsConnection(self.store)

    @staticmethod
    def _table_columns_stub(_conn, table_name):
        supported = {
            "app_settings",
            "audit_log",
            "recognition_events",
            "user_embeddings",
            "users",
        }
        if table_name in supported:
            return {"id"}
        return set()

    def _log_action_stub(self, action, target=None):
        self.store["audit_counter"] += 1
        self.store["audit_log"].append(
            {
                "audit_id": self.store["audit_counter"],
                "staff_id": session.get("staff_id"),
                "username": session.get("username"),
                "action": action,
                "target": target or "",
                "ip_address": "127.0.0.1",
                "timestamp": f"2026-05-05 10:00:{self.store['audit_counter']:02d}",
            }
        )

    @staticmethod
    def _set_session(client, role: str):
        with client.session_transaction() as sess:
            sess["staff_id"] = 10
            sess["username"] = f"{role}_user"
            sess["role"] = role

    def test_library_staff_can_read_settings_but_cannot_post(self):
        client = self._build_client()
        self._set_session(client, "library_staff")

        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ), patch("routes.routes.bump_settings_version", return_value=2), patch(
            "routes.routes.log_action", side_effect=self._log_action_stub
        ):
            response = client.get("/api/settings")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertFalse(payload["permissions"]["can_save"])
            self.assertIn("bounds", payload)
            post_response = client.post("/api/settings", json={"max_occupancy": 350})
            self.assertEqual(post_response.status_code, 403)

    def test_library_admin_safe_fields_only_and_bounds_validation(self):
        client = self._build_client()
        self._set_session(client, "library_admin")

        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ), patch("routes.routes.bump_settings_version", return_value=2), patch(
            "routes.routes.log_action", side_effect=self._log_action_stub
        ):
            response = client.post("/api/settings", json={"max_occupancy": 420, "vector_index_top_k": 35})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["max_occupancy"], 420)
            self.assertEqual(payload["vector_index_top_k"], 35)

            forbidden_response = client.post("/api/settings", json={"threshold": 0.55})
            self.assertEqual(forbidden_response.status_code, 403)

            bad_occupancy = client.post("/api/settings", json={"max_occupancy": 10})
            self.assertEqual(bad_occupancy.status_code, 400)

            bad_top_k = client.post("/api/settings", json={"vector_index_top_k": 101})
            self.assertEqual(bad_top_k.status_code, 400)

    def test_super_admin_can_update_all_fields_and_get_audit_metadata(self):
        client = self._build_client()
        self._set_session(client, "super_admin")

        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ), patch("routes.routes.bump_settings_version", return_value=2), patch(
            "routes.routes.log_action", side_effect=self._log_action_stub
        ):
            response = client.post(
                "/api/settings",
                json={
                    "threshold": 0.44,
                    "quality_threshold": 0.36,
                    "vector_index_top_k": 25,
                    "max_occupancy": 360,
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["threshold"], 0.44)
            self.assertEqual(payload["quality_threshold"], 0.36)
            self.assertEqual(payload["vector_index_top_k"], 25)
            self.assertEqual(payload["max_occupancy"], 360)
            self.assertTrue(payload["audit_rows"])
            self.assertIsNotNone(payload["last_change"])
            self.assertIn("threshold", payload["last_change"]["target"])
            self.assertIn("max_occupancy", payload["last_change"]["target"])

    def test_destructive_endpoints_are_super_admin_only(self):
        client = self._build_client()
        self._set_session(client, "library_admin")

        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ):
            reset_forbidden = client.post("/api/reset_database")
            clear_forbidden = client.post("/api/clear_log")
            self.assertEqual(reset_forbidden.status_code, 403)
            self.assertEqual(clear_forbidden.status_code, 403)

        self._set_session(client, "super_admin")
        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ):
            clear_allowed = client.post("/api/clear_log")
            self.assertEqual(clear_allowed.status_code, 200)
            self.assertEqual(self.store["stats_rows"], [])

            self.store["users"] = [1, 2]
            self.store["user_embeddings"] = [101]
            reset_allowed = client.post("/api/reset_database")
            self.assertEqual(reset_allowed.status_code, 200)
            self.assertEqual(self.store["users"], [])
            self.assertEqual(self.store["user_embeddings"], [])

    def test_library_staff_can_access_detailed_stats(self):
        client = self._build_client()
        self._set_session(client, "library_staff")

        with patch("routes.routes.db_connect", side_effect=self._db_connect_stub), patch(
            "routes.routes.table_columns", side_effect=self._table_columns_stub
        ):
            response = client.get("/api/stats")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertIn("recognition_stats", payload)
            self.assertEqual(payload["user_count"], len(self.store["users"]))


if __name__ == "__main__":
    unittest.main()
