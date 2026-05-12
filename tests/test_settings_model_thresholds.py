import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask

from core.config import AppConfig

realtime_stub = sys.modules.setdefault("app.realtime", types.ModuleType("app.realtime"))
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
realtime_stub.emit_capacity_threshold_alert = lambda *args, **kwargs: None
realtime_stub.emit_unrecognized_detection = lambda *args, **kwargs: None

from routes.routes import create_routes_blueprint


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._rows = []
        self._row = None

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).lower().split())
        params = params or ()
        self._rows = []
        self._row = None

        if "select value from app_settings where key" in normalized:
            key = params[0]
            value = self.store["settings"].get(key)
            self._row = (value,) if value is not None else None
            return

        if "insert into app_settings" in normalized:
            key, value = params[0], params[1]
            self.store["settings"][key] = str(value)
            return

        if "select count(*) from users" in normalized:
            self._row = (self.store.get("user_count", 0),)
            return

        if "from audit_log" in normalized and "select" in normalized:
            self._rows = []
            return

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass

    def close(self):
        pass


class SettingsModelThresholdTests(unittest.TestCase):
    def _build_client(self, role="super_admin", settings=None):
        config = AppConfig()
        thresholds = {"base": 0.75, "quality": 0.8}
        store = {
            "settings": dict(settings or {}),
            "user_count": 3,
        }
        app = Flask(__name__)
        app.secret_key = "test-secret"

        def get_thresholds():
            return thresholds["base"], thresholds["quality"]

        def set_thresholds(threshold, quality_threshold):
            thresholds["base"] = float(threshold)
            thresholds["quality"] = float(quality_threshold)
            config.base_threshold = float(threshold)
            config.face_quality_threshold = float(quality_threshold)

        patches = [
            patch("routes.routes.db_connect", side_effect=lambda _db_path: _FakeConnection(store)),
            patch("routes.routes.table_columns", return_value={"key", "value"}),
            patch("routes.routes.ensure_version_settings", return_value=None),
            patch("routes.routes.bump_settings_version", return_value=2),
            patch("routes.routes.log_action", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        app.register_blueprint(
            create_routes_blueprint(
                {
                    "config": config,
                    "db_path": "postgresql://test",
                    "get_thresholds": get_thresholds,
                    "set_thresholds": set_thresholds,
                    "repository": None,
                }
            )
        )
        client = app.test_client()
        with client.session_transaction() as session:
            session["staff_id"] = 1
            session["username"] = "admin"
            session["role"] = role
        return client, config, store, thresholds

    def test_get_settings_returns_base_and_model_thresholds(self):
        client, _config, _store, _thresholds = self._build_client(
            settings={
                "threshold": "0.74",
                "primary_threshold": "0.81",
                "secondary_threshold": "0.79",
            }
        )

        response = client.get("/api/settings/recognition")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["threshold"], 0.74)
        self.assertEqual(payload["primary_threshold"], 0.81)
        self.assertEqual(payload["secondary_threshold"], 0.79)

    def test_super_admin_post_persists_and_applies_model_thresholds(self):
        client, config, store, thresholds = self._build_client()

        response = client.post(
            "/api/settings/recognition",
            json={
                "threshold": "0.76",
                "primary_threshold": "0.82",
                "secondary_threshold": "0.78",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(store["settings"]["threshold"], "0.76")
        self.assertEqual(store["settings"]["primary_threshold"], "0.82")
        self.assertEqual(store["settings"]["secondary_threshold"], "0.78")
        self.assertEqual(thresholds["base"], 0.76)
        self.assertEqual(config.primary_threshold, 0.82)
        self.assertEqual(config.secondary_threshold, 0.78)

    def test_library_admin_cannot_modify_model_or_base_thresholds(self):
        client, _config, _store, _thresholds = self._build_client(role="library_admin")

        response = client.post(
            "/api/settings/recognition",
            json={"threshold": "0.76", "primary_threshold": "0.82"},
        )

        self.assertEqual(response.status_code, 403)

    def test_out_of_range_model_threshold_returns_400(self):
        client, _config, _store, _thresholds = self._build_client()

        response = client.post(
            "/api/settings/recognition",
            json={"primary_threshold": "0.99"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("primary_threshold", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
