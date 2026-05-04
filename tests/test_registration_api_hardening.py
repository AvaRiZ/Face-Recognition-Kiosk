from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from flask import Flask

from core.config import AppConfig
from core.state import AppStateManager

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


@unittest.skipIf(create_routes_blueprint is None, "Route blueprint dependencies are unavailable.")
class RegistrationApiHardeningTests(unittest.TestCase):
    def _build_client(
        self,
        *,
        worker_attached: bool = True,
        stream_state: str = "live",
        stream_message: str = "Camera stream active.",
        detection_paused: bool = False,
        session_timeout_seconds: int = 180,
    ):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)
        db_path = str(temp_path / "test.db")

        config = AppConfig()
        config.db_path = db_path
        config.base_save_dir = str(temp_path / "faces")
        config.registration_session_timeout_seconds = int(session_timeout_seconds)
        state = AppStateManager(config)
        self._last_state = state

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "config": config,
            "db_path": db_path,
            "base_save_dir": config.base_save_dir,
            "repository": _DummyRepository(),
            "worker_runtime_attached": worker_attached,
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
            "detection_paused": lambda: detection_paused,
            "stream_status": lambda: {"state": stream_state, "message": stream_message},
            "yolo_model": None,
            "yolo_device": "cpu",
        }
        app.register_blueprint(create_routes_blueprint(deps))
        return app.test_client()

    @staticmethod
    def _set_session(client, role: str) -> None:
        with client.session_transaction() as sess:
            sess["staff_id"] = 1
            sess["username"] = "tester"
            sess["role"] = role

    def test_register_info_requires_authentication(self) -> None:
        client = self._build_client()
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 401)

    def test_register_info_rejects_forbidden_role(self) -> None:
        client = self._build_client()
        self._set_session(client, "guest")
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 403)

    def test_register_info_includes_status_reason_fields(self) -> None:
        client = self._build_client(stream_state="reconnecting", stream_message="Retrying camera link.")
        self._set_session(client, "library_staff")
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("status_reason_code", payload)
        self.assertIn("status_reason_message", payload)
        self.assertIn("status_updated_at", payload)
        self.assertEqual(payload.get("status_reason_code"), "stream_reconnecting")
        self.assertEqual(payload.get("status_reason_message"), "Retrying camera link.")

    def test_register_info_includes_session_timing_fields(self) -> None:
        client = self._build_client()
        self._set_session(client, "library_staff")
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("session_timeout_seconds", payload)
        self.assertIn("session_started_at", payload)
        self.assertIn("last_activity_at", payload)
        self.assertIn("session_expires_at", payload)
        self.assertIn("seconds_until_expiry", payload)
        self.assertIsInstance(payload.get("session_timeout_seconds"), int)

    def test_register_info_expiry_countdown_above_warning_threshold(self) -> None:
        client = self._build_client(session_timeout_seconds=300)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsInstance(payload.get("seconds_until_expiry"), int)
        self.assertGreater(payload.get("seconds_until_expiry"), 120)

    def test_register_info_expiry_countdown_at_or_below_warning_threshold(self) -> None:
        client = self._build_client(session_timeout_seconds=120)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsInstance(payload.get("seconds_until_expiry"), int)
        self.assertLessEqual(payload.get("seconds_until_expiry"), 120)

    def test_register_info_marks_expired_session_and_zeroes_countdown(self) -> None:
        client = self._build_client(session_timeout_seconds=120)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        self._last_state.registration_state.last_activity_at = time.time() - 200
        response = client.get("/api/register-info")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload.get("session_expired"))
        self.assertEqual(payload.get("seconds_until_expiry"), 0)

    def test_start_session_reports_worker_unattached_reason(self) -> None:
        client = self._build_client(worker_attached=False)
        self._set_session(client, "library_admin")
        response = client.post("/api/register-session/start")
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload.get("status_reason_code"), "worker_unattached")

    def test_register_submit_requires_authentication(self) -> None:
        client = self._build_client()
        response = client.post("/register", data={})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
