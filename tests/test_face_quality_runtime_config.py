import unittest
import sys
import types
from unittest.mock import patch

from flask import Flask

from core.config import AppConfig

realtime_stub = types.ModuleType("app.realtime")
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
realtime_stub.emit_capacity_threshold_alert = lambda *args, **kwargs: None
realtime_stub.emit_unrecognized_detection = lambda *args, **kwargs: None
sys.modules.setdefault("app.realtime", realtime_stub)

from routes.internal_routes import create_internal_blueprint


class FaceQualityRuntimeConfigTests(unittest.TestCase):
    def test_internal_runtime_config_includes_all_quality_profiles(self):
        config = AppConfig()
        app = Flask(__name__)
        app.register_blueprint(
            create_internal_blueprint(
                {
                    "config": config,
                    "db_path": "unused",
                    "repository": None,
                    "worker_runtime_attached": False,
                    "get_thresholds": lambda: (0.7, 0.8),
                    "get_registration_control": lambda: {
                        "session_id": None,
                        "phase": "idle",
                        "expected_pose": None,
                        "force_new_identity": False,
                        "registration_kind": "student",
                    },
                }
            )
        )

        def fake_get_setting(_db_path, key, default=None):
            if key == "exit_quality_quality_sharpness_min":
                return "55"
            return default

        with patch("routes.internal_routes.get_settings_version", return_value=12), patch(
            "routes.internal_routes.get_app_setting",
            side_effect=fake_get_setting,
        ):
            response = app.test_client().get("/api/internal/runtime-config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["settings_version"], 12)
        self.assertEqual(payload["primary_threshold"], config.primary_threshold)
        self.assertEqual(payload["secondary_threshold"], config.secondary_threshold)
        self.assertEqual(payload["online_learning_confidence_threshold"], config.online_learning_confidence_threshold)
        self.assertEqual(
            payload["cli_model_confidence_display_enabled"],
            config.cli_model_confidence_display_enabled,
        )
        self.assertEqual(set(payload["face_quality_profiles"]), {"entry", "exit", "registration"})
        self.assertEqual(payload["face_quality_profiles"]["exit"]["quality_sharpness_min"], 55.0)


if __name__ == "__main__":
    unittest.main()
