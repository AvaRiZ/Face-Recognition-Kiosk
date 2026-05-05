from __future__ import annotations

import base64
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from flask import Flask

from app.cli import CLIApplication
from core.config import AppConfig
from core.models import RegistrationSample
from core.state import AppStateManager
from services.tracking_service import TrackingService

try:
    from routes.routes import create_routes_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_routes_blueprint = None

try:
    from routes.internal_routes import create_internal_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_internal_blueprint = None


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


@unittest.skipIf(create_routes_blueprint is None, "Route blueprint dependencies are unavailable.")
class RegistrationApiHardeningTests(unittest.TestCase):
    def _build_client(
        self,
        *,
        worker_attached: bool = True,
        entry_worker_online: bool = True,
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
        config.registration_worker_heartbeat_ttl_seconds = 10
        state = AppStateManager(config)
        self._last_state = state
        if entry_worker_online:
            state.record_worker_heartbeat(worker_role="entry", station_id="entry-station-1", camera_id=1)

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
            "claim_registration_sample_id": state.claim_registration_sample_id,
            "get_current_registration_pose": state.get_current_registration_pose,
            "get_registration_progress": state.get_registration_progress,
            "is_registration_ready": state.is_registration_ready,
            "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
            "reset_database_state": state.reset_database_state,
            "reset_registration_state": state.reset_registration_state,
            "start_web_registration_session": state.start_web_registration_session,
            "cancel_web_registration_session": state.cancel_web_registration_session,
            "record_worker_heartbeat": state.record_worker_heartbeat,
            "get_worker_last_seen_at": state.get_worker_last_seen_at,
            "is_worker_online": state.is_worker_online,
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
        if create_internal_blueprint is not None:
            app.register_blueprint(create_internal_blueprint(deps))
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
        client = self._build_client(worker_attached=False, entry_worker_online=False)
        self._set_session(client, "library_admin")
        response = client.post("/api/register-session/start")
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload.get("status_reason_code"), "worker_unattached")

    def test_start_session_succeeds_with_fresh_entry_worker_heartbeat(self) -> None:
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_admin")
        response = client.post("/api/register-session/start")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload.get("success"))

    def _sample_payload(self, *, sample_id: str, session_id: str, worker_role: str = "entry") -> dict:
        image = np.full((32, 32, 3), 150, dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        return {
            "sample_id": sample_id,
            "session_id": session_id,
            "pose": "front",
            "quality": 0.92,
            "face_jpeg_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
            "embeddings": {"ArcFace": [[0.1, 0.2, 0.3]], "Facenet": [[0.4, 0.5, 0.6]]},
            "captured_at": "2026-05-05T08:00:00Z",
            "worker_role": worker_role,
            "station_id": "entry-station-1",
            "camera_id": 1,
        }

    def test_registration_sample_ingest_increments_progress(self) -> None:
        if create_internal_blueprint is None:
            self.skipTest("Internal route blueprint dependencies are unavailable.")
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        session_id = self._last_state.registration_state.session_id
        self.assertTrue(session_id)

        response = client.post("/api/internal/registration-samples", json=self._sample_payload(sample_id="s-1", session_id=session_id))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload.get("success"))
        self.assertFalse(payload.get("duplicate"))
        self.assertGreaterEqual(int(self._last_state.registration_state.capture_count), 1)

    def test_registration_sample_ingest_rejects_stale_session(self) -> None:
        if create_internal_blueprint is None:
            self.skipTest("Internal route blueprint dependencies are unavailable.")
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)

        response = client.post(
            "/api/internal/registration-samples",
            json=self._sample_payload(sample_id="s-stale", session_id="stale-session"),
        )
        self.assertEqual(response.status_code, 409)

    def test_registration_sample_ingest_is_idempotent_by_sample_id(self) -> None:
        if create_internal_blueprint is None:
            self.skipTest("Internal route blueprint dependencies are unavailable.")
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        session_id = self._last_state.registration_state.session_id
        self.assertTrue(session_id)

        payload = self._sample_payload(sample_id="s-dup", session_id=session_id)
        first = client.post("/api/internal/registration-samples", json=payload)
        self.assertEqual(first.status_code, 200)
        before = int(self._last_state.registration_state.capture_count)
        second = client.post("/api/internal/registration-samples", json=payload)
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertTrue(second_payload.get("duplicate"))
        after = int(self._last_state.registration_state.capture_count)
        self.assertEqual(before, after)

    def test_registration_sample_ingest_rejects_non_entry_worker(self) -> None:
        if create_internal_blueprint is None:
            self.skipTest("Internal route blueprint dependencies are unavailable.")
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        session_id = self._last_state.registration_state.session_id
        self.assertTrue(session_id)
        response = client.post(
            "/api/internal/registration-samples",
            json=self._sample_payload(sample_id="s-exit", session_id=session_id, worker_role="exit"),
        )
        self.assertEqual(response.status_code, 403)

    def test_register_submit_succeeds_after_internal_sample_ingest(self) -> None:
        if create_internal_blueprint is None:
            self.skipTest("Internal route blueprint dependencies are unavailable.")
        client = self._build_client(entry_worker_online=True)
        self._set_session(client, "library_staff")
        started = client.post("/api/register-session/start")
        self.assertEqual(started.status_code, 200)
        session_id = self._last_state.registration_state.session_id
        self.assertTrue(session_id)

        target_captures = int(self._last_state.registration_state.max_captures)
        for index in range(target_captures):
            response = client.post(
                "/api/internal/registration-samples",
                json=self._sample_payload(sample_id=f"s-{index}", session_id=session_id),
            )
            self.assertEqual(response.status_code, 200)

        submit = client.post(
            "/register",
            data={
                "name": "Doe, Jane",
                "sr_code": "23-12345",
                "gender": "Female",
                "program": "Bachelor of Science in Computer Science",
            },
        )
        self.assertEqual(submit.status_code, 200)
        payload = submit.get_json()
        self.assertTrue(payload.get("success"))


class RegistrationLockingTests(unittest.TestCase):
    def setUp(self) -> None:
        config = AppConfig()
        config.registration_min_face_area = 100
        self.config = config
        self.state = AppStateManager(config)
        self.tracking_service = TrackingService(config, self.state)
        self.cli = CLIApplication(
            config,
            self.state,
            _DummyRepository(),
            quality_service=None,
            recognition_service=None,
            tracking_service=self.tracking_service,
            yolo_model=None,
            yolo_device="cpu",
        )

    def _track(self, track_id: int, *, area: int, stable: bool = True, recognized: bool = False):
        track_state = self.state.initialize_track_state(track_id, time.time())
        track_state.last_stable = stable
        track_state.last_area = area
        track_state.recognized = recognized
        return track_state

    def test_sticky_lock_keeps_current_candidate(self) -> None:
        self._track(1, area=400)
        self._track(2, area=800)
        self.state.registration_state.selected_track_id = 1

        selected = self.cli._select_registration_candidate([1, 2])

        self.assertEqual(selected, 1)
        self.assertTrue(self.state.get_track_state(1).selected_for_registration)
        self.assertFalse(self.state.get_track_state(2).selected_for_registration)

    def test_sticky_lock_moves_to_largest_when_ineligible(self) -> None:
        self.config.registration_min_face_area = 500
        self._track(1, area=400)
        self._track(2, area=800)
        self.state.registration_state.selected_track_id = 1

        selected = self.cli._select_registration_candidate([1, 2])

        self.assertEqual(selected, 2)
        self.assertTrue(self.state.get_track_state(2).selected_for_registration)


class RegistrationConfirmCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        config = AppConfig()
        config.registration_recognition_confirm_frames = 2
        self.config = config
        self.state = AppStateManager(config)
        self.tracking_service = TrackingService(config, self.state)
        self.cli = CLIApplication(
            config,
            self.state,
            _DummyRepository(),
            quality_service=None,
            recognition_service=None,
            tracking_service=self.tracking_service,
            yolo_model=None,
            yolo_device="cpu",
        )

    def _start_manual_lock(self, track_id: int = 1):
        self.state.start_manual_registration(track_id)
        track_state = self.state.initialize_track_state(track_id, time.time())
        track_state.user = {"name": "Alice"}
        return track_state

    def test_confirm_then_cancel_clears_samples(self) -> None:
        track_state = self._start_manual_lock()
        sample = RegistrationSample(
            face_crop=np.zeros((2, 2, 3), dtype=np.uint8),
            embeddings={},
            quality=0.9,
            pose="front",
        )
        self.state.registration_state.captured_samples = [sample]
        result = {"status": "recognized", "match_confidence": 0.92, "match_threshold": 0.85}

        self.cli._handle_existing_recognition_during_registration(track_state, result)
        self.assertTrue(self.state.registration_state.manual_active)
        self.cli._handle_existing_recognition_during_registration(track_state, result)

        self.assertFalse(self.state.registration_state.manual_active)
        self.assertEqual(self.state.registration_state.captured_samples, [])
        self.assertEqual(self.state.registration_state.status_reason_code, "recognized_existing")

    def test_confirm_resets_streak_when_below_threshold(self) -> None:
        track_state = self._start_manual_lock()
        track_state.registration_recognized_name = "Alice"
        track_state.registration_recognized_streak = 2
        result = {"status": "recognized", "match_confidence": 0.71, "match_threshold": 0.85}

        self.cli._handle_existing_recognition_during_registration(track_state, result)

        self.assertEqual(track_state.registration_recognized_streak, 0)
        self.assertEqual(track_state.registration_recognized_name, "Alice")

    def test_confirm_resets_streak_on_identity_change(self) -> None:
        track_state = self._start_manual_lock()
        track_state.registration_recognized_name = "Alice"
        track_state.registration_recognized_streak = 2
        track_state.user = {"name": "Bob"}
        result = {"status": "recognized", "match_confidence": 0.92, "match_threshold": 0.85}

        self.cli._handle_existing_recognition_during_registration(track_state, result)

        self.assertEqual(track_state.registration_recognized_name, "Bob")
        self.assertEqual(track_state.registration_recognized_streak, 1)

    def test_override_keeps_registration_active(self) -> None:
        track_state = self._start_manual_lock()
        self.state.enable_unknown_registration_override()
        result = {"status": "recognized", "match_confidence": 0.92, "match_threshold": 0.85}

        self.cli._handle_existing_recognition_during_registration(track_state, result)

        self.assertTrue(self.state.registration_state.manual_active)
        self.assertEqual(self.state.registration_state.status_reason_code, "override_forced_unknown")
        self.assertEqual(track_state.registration_recognized_streak, 0)


class RegistrationStateTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        config = AppConfig()
        config.registration_session_timeout_seconds = 1
        self.state = AppStateManager(config)

    def test_manual_registration_transitions_set_flags(self) -> None:
        self.state.request_manual_registration()
        reg_state = self.state.registration_state
        self.assertTrue(reg_state.manual_requested)
        self.assertFalse(reg_state.manual_active)
        self.assertFalse(reg_state.session_active)
        self.assertFalse(reg_state.session_expired)
        self.assertIsNone(reg_state.session_id)

        self.state.start_manual_registration(5)
        reg_state = self.state.registration_state
        self.assertFalse(reg_state.manual_requested)
        self.assertTrue(reg_state.manual_active)
        self.assertEqual(reg_state.manual_track_id, 5)
        self.assertEqual(reg_state.selected_track_id, 5)

    def test_stop_manual_registration_clears_session_when_not_ready(self) -> None:
        self.state.start_manual_registration(3)
        self.state.stop_manual_registration()
        reg_state = self.state.registration_state
        self.assertFalse(reg_state.manual_active)
        self.assertFalse(reg_state.manual_requested)
        self.assertIsNone(reg_state.manual_track_id)
        self.assertIsNone(reg_state.session_id)
        self.assertIsNone(reg_state.session_started_at)
        self.assertIsNone(reg_state.last_activity_at)

    def test_start_and_cancel_web_registration_session(self) -> None:
        started = self.state.start_web_registration_session()
        self.assertTrue(started)
        reg_state = self.state.registration_state
        self.assertTrue(reg_state.session_active)
        self.assertTrue(reg_state.manual_requested)
        self.assertTrue(reg_state.session_id)

        self.state.cancel_web_registration_session()
        reg_state = self.state.registration_state
        self.assertFalse(reg_state.session_active)
        self.assertFalse(reg_state.manual_requested)
        self.assertIsNone(reg_state.session_id)
        self.assertEqual(reg_state.status_reason_code, "session_canceled")

    def test_expire_registration_session_clears_flags(self) -> None:
        self.state.start_web_registration_session()
        reg_state = self.state.registration_state
        reg_state.last_activity_at = time.time() - 5
        expired = self.state.expire_registration_session_if_needed()

        self.assertTrue(expired)
        reg_state = self.state.registration_state
        self.assertTrue(reg_state.session_expired)
        self.assertFalse(reg_state.session_active)
        self.assertIsNone(reg_state.session_id)

    def test_register_submit_requires_authentication(self) -> None:
        client = self._build_client()
        response = client.post("/register", data={})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
