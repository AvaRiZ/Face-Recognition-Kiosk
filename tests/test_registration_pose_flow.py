import unittest
import tempfile
import sys
import types

import numpy as np

realtime_stub = types.ModuleType("app.realtime")
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
sys.modules.setdefault("app.realtime", realtime_stub)

from app.cli import CLIApplication
from core.config import AppConfig
from core.models import RegistrationSample, User
from core.state import AppStateManager
from services.recognition_service import FaceRecognitionService
from workers.durable_queue import DurableOutboundQueue
from workers.worker_repository import WorkerApiRepository


def _sample(pose: str) -> RegistrationSample:
    return RegistrationSample(
        face_crop=np.zeros((8, 8, 3), dtype=np.uint8),
        embeddings={},
        quality=1.0,
        pose=pose,
    )


class _FailingEmbeddingService:
    def extract_embedding_ensemble(self, face_crop):
        raise AssertionError("Embedding extraction should not run for a wrong registration pose")


class _RecordingEmbeddingService:
    def __init__(self, config):
        self.config = config
        self.calls = 0

    def extract_embedding_ensemble(self, face_crop):
        self.calls += 1
        return {
            self.config.primary_model: [np.ones(4, dtype=np.float32)],
            self.config.secondary_model: [np.ones(4, dtype=np.float32)],
        }


class _NoopRepository:
    camera_id = 1

    def log_recognition(self, *args, **kwargs):
        raise AssertionError("Recognition logging should not run for a wrong registration pose")

    def update_embeddings(self, *args, **kwargs):
        return None


class _BlockingPresenceRepository(_NoopRepository):
    def __init__(self):
        self.log_called = False

    def check_presence_gate(self, user_id: int, event_type: str) -> dict:
        return {
            "success": True,
            "user_id": int(user_id),
            "event_type": str(event_type),
            "allow_event": False,
            "reason": "already_inside",
            "inside_now": True,
        }

    def log_recognition(self, *args, **kwargs):
        self.log_called = True
        raise AssertionError("Presence-gated recognitions must not be logged")

    def update_embeddings(self, *args, **kwargs):
        raise AssertionError("Blocked recognitions should not update embeddings")


class _PoseMismatchQualityService:
    def assess_face_quality(self, *args, **kwargs):
        return 1.0, "Good", {"failed_checks": [], "component_scores": {}}

    def detect_face_pose(self, *args, **kwargs):
        return "right"

    def quality_debug_summary(self, debug_info):
        return ""


class _RecordingApiClient:
    def __init__(self):
        self.posts = []

    def post_json(self, path, payload):
        self.posts.append((path, payload))
        return {"success": True}


class RegistrationPoseFlowTests(unittest.TestCase):
    def _state(self) -> AppStateManager:
        config = AppConfig()
        config.registration_samples_per_pose_target = 3
        config.registration_retained_samples_per_pose = 3
        return AppStateManager(config)

    def test_registration_pose_does_not_advance_on_wrong_pose_sample(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        self.assertEqual(state.get_current_registration_pose(), "front")

        for _ in range(3):
            state.capture_registration_sample(_sample("front"))

        self.assertEqual(state.get_current_registration_pose(), "left")
        self.assertEqual(state.get_pose_capture_count("left"), 0)

        capture_count = state.capture_registration_sample(_sample("right"))

        self.assertEqual(capture_count, 3)
        self.assertEqual(state.get_current_registration_pose(), "left")
        self.assertEqual(state.get_pose_capture_count("left"), 0)
        self.assertEqual(state.get_pose_capture_count("right"), 0)

    def test_registration_pose_advances_after_required_samples_for_current_pose(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())

        for _ in range(3):
            state.capture_registration_sample(_sample("front"))
        for _ in range(2):
            state.capture_registration_sample(_sample("left"))

        self.assertEqual(state.get_current_registration_pose(), "left")
        self.assertEqual(state.get_pose_capture_count("left"), 2)

        state.capture_registration_sample(_sample("left"))

        self.assertEqual(state.get_current_registration_pose(), "right")
        self.assertEqual(state.get_pose_capture_count("left"), 3)

    def test_registration_control_sync_updates_pose_counts_from_server(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        state.sync_registration_control(
            session_id=state.registration_state.session_id,
            phase="capturing",
            expected_pose="left",
            pose_capture_counts={"front": 3, "left": 2, "right": 0},
        )

        self.assertEqual(state.get_current_registration_pose(), "left")
        self.assertEqual(state.get_pose_capture_count("front"), 3)
        self.assertEqual(state.get_pose_capture_count("left"), 2)
        self.assertEqual(state.get_pose_capture_count("right"), 0)

    def test_registration_control_sync_does_not_regress_local_pose_progress(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        for _ in range(3):
            state.capture_registration_sample(_sample("front"))

        self.assertEqual(state.get_current_registration_pose(), "left")
        state.sync_registration_control(
            session_id=state.registration_state.session_id,
            phase="capturing",
            expected_pose="front",
            pose_capture_counts={"front": 2, "left": 0, "right": 0},
        )

        self.assertEqual(state.get_current_registration_pose(), "left")
        self.assertEqual(state.get_pose_capture_count("front"), 3)

    def test_registration_control_sync_does_not_reopen_locally_ready_capture(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        for pose in ("front", "left", "right"):
            for _ in range(3):
                state.capture_registration_sample(_sample(pose))

        self.assertEqual(state.registration_state.phase, "ready")
        state.sync_registration_control(
            session_id=state.registration_state.session_id,
            phase="capturing",
            expected_pose="right",
            pose_capture_counts={"front": 3, "left": 3, "right": 2},
        )

        self.assertEqual(state.registration_state.phase, "ready")
        self.assertTrue(state.is_registration_ready())

    def test_registration_lock_does_not_skip_non_target_tracks(self):
        state = self._state()
        self.assertTrue(state.start_registration_session())
        state.start_manual_registration(10)

        self.assertFalse(
            CLIApplication._should_skip_track_during_registration(
                registration_enabled=True,
                reg_state=state.registration_state,
                locked_track_id=10,
                track_id=11,
            )
        )

    def test_registration_pose_mismatch_skips_embedding_extraction(self):
        config = AppConfig()
        state = AppStateManager(config)
        service = FaceRecognitionService(
            config=config,
            state=state,
            repository=_NoopRepository(),
            embedding_service=_FailingEmbeddingService(),
        )

        self.assertTrue(state.start_registration_session())
        result = service.register_or_recognize_face(
            np.zeros((260, 260, 3), dtype=np.uint8),
            quality_service=_PoseMismatchQualityService(),
            allow_registration=True,
            precomputed_quality=(1.0, "Good", {"failed_checks": [], "component_scores": {}}),
            quality_context="entry",
            registration_quality=(1.0, "Good", {"failed_checks": [], "component_scores": {}}),
        )

        self.assertEqual(result["status"], "pose_mismatch")
        self.assertEqual(result["expected_pose"], "front")
        self.assertEqual(result["detected_pose"], "right")

    def test_ready_registration_session_still_allows_recognition(self):
        config = AppConfig()
        state = AppStateManager(config)
        embedding_service = _RecordingEmbeddingService(config)
        service = FaceRecognitionService(
            config=config,
            state=state,
            repository=_NoopRepository(),
            embedding_service=embedding_service,
        )

        self.assertTrue(state.start_registration_session())
        for pose in ("front", "left", "right"):
            for _ in range(int(config.registration_samples_per_pose_target)):
                state.capture_registration_sample(_sample(pose))

        self.assertEqual(state.registration_state.phase, "ready")
        result = service.register_or_recognize_face(
            np.zeros((260, 260, 3), dtype=np.uint8),
            quality_service=_PoseMismatchQualityService(),
            allow_registration=False,
            precomputed_quality=(1.0, "Good", {"failed_checks": [], "component_scores": {}}),
            quality_context="entry",
        )

        self.assertEqual(embedding_service.calls, 1)
        self.assertEqual(result["status"], "no_match")

    def test_presence_gate_blocked_match_still_sets_recognized_payload_without_logging(self):
        config = AppConfig()
        config.primary_threshold = 0.5
        config.secondary_threshold = 0.5
        config.recognition_confidence_threshold = 0.5
        state = AppStateManager(config)
        state.load_users(
            [
                User(
                    id=42,
                    name="Ada Lovelace",
                    sr_code="SR-42",
                    gender="Female",
                    program="Computer Science",
                    embeddings={
                        config.primary_model: [np.ones(4, dtype=np.float32)],
                        config.secondary_model: [np.ones(4, dtype=np.float32)],
                    },
                )
            ]
        )
        repository = _BlockingPresenceRepository()
        service = FaceRecognitionService(
            config=config,
            state=state,
            repository=repository,
            embedding_service=_RecordingEmbeddingService(config),
        )

        result = service.register_or_recognize_face(
            np.zeros((260, 260, 3), dtype=np.uint8),
            quality_service=_PoseMismatchQualityService(),
            allow_registration=False,
            precomputed_quality=(1.0, "Good", {"failed_checks": [], "component_scores": {}}),
            quality_context="entry",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason_code"], "already_inside")
        self.assertEqual(result["payload"]["name"], "Ada Lovelace")
        self.assertEqual(state.recognized_user["name"], "Ada Lovelace")
        self.assertFalse(repository.log_called)

    def test_registration_samples_remain_in_memory_until_completion(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        for pose in ("front", "left", "right"):
            for _ in range(3):
                state.capture_registration_sample(_sample(pose))

        self.assertEqual(state.registration_state.phase, "ready")
        self.assertTrue(state.is_registration_ready())
        self.assertEqual(len(state.registration_state.captured_samples), 9)

        state.complete_registration()

        self.assertEqual(state.registration_state.phase, "idle")
        self.assertEqual(state.registration_state.captured_samples, [])
        self.assertEqual(state.registration_state.pose_capture_counts, {"front": 0, "left": 0, "right": 0})

    def test_registration_samples_are_purged_on_cancel(self):
        state = self._state()

        self.assertTrue(state.start_registration_session())
        state.capture_registration_sample(_sample("front"))

        self.assertEqual(len(state.registration_state.captured_samples), 1)
        state.cancel_registration_session()

        self.assertEqual(state.registration_state.captured_samples, [])
        self.assertEqual(state.registration_state.pose_capture_counts, {"front": 0, "left": 0, "right": 0})

    def test_registration_samples_are_purged_on_expiry(self):
        state = self._state()
        state._config.registration_session_timeout_seconds = 1

        self.assertTrue(state.start_registration_session())
        state.capture_registration_sample(_sample("front"))
        state.registration_state.expires_at = 0

        self.assertTrue(state.expire_registration_session_if_needed())
        self.assertEqual(state.registration_state.phase, "expired")
        self.assertEqual(state.registration_state.captured_samples, [])
        self.assertEqual(state.registration_state.pose_capture_counts, {"front": 0, "left": 0, "right": 0})

    def test_worker_repository_has_no_registration_sample_enqueue_api(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            api_client = _RecordingApiClient()
            queue = DurableOutboundQueue(queue_dir)
            repository = WorkerApiRepository(
                api_client=api_client,
                outbound_queue=queue,
                station_id="entry-station-1",
                camera_id=1,
            )

            self.assertFalse(hasattr(repository, "enqueue_registration_sample"))
            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 0)

    def test_worker_repository_posts_registration_sample_without_queueing(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            api_client = _RecordingApiClient()
            queue = DurableOutboundQueue(queue_dir)
            repository = WorkerApiRepository(
                api_client=api_client,
                outbound_queue=queue,
                station_id="entry-station-1",
                camera_id=1,
            )

            response = repository.post_registration_sample(
                sample_id="sample-1",
                session_id="session-1",
                pose="front",
                quality=0.95,
                face_crop=np.zeros((24, 24, 3), dtype=np.uint8),
                embeddings={"ArcFace": [np.ones(4, dtype=np.float32)]},
            )

            self.assertEqual(response, {"success": True})
            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 0)
            self.assertEqual(len(api_client.posts), 1)
            self.assertEqual(api_client.posts[0][0], "/api/internal/registrations/samples")
            self.assertIn("face_jpeg_base64", api_client.posts[0][1])
            self.assertEqual(api_client.posts[0][1]["sample_id"], "sample-1")

    def test_durable_queue_no_longer_blocks_registration_sample_entries(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            queue = DurableOutboundQueue(queue_dir)
            queue.enqueue("registration_sample", {"session_id": "session-1", "pose": "front"})
            queue.enqueue("registration_sample", {"session_id": "session-1", "pose": "left"})
            sent_poses = []

            def sender(entry):
                sent_poses.append(entry["payload"]["pose"])
                return False

            sent, remaining = queue.drain_once(sender)

            self.assertEqual(sent, 0)
            self.assertEqual(remaining, 2)
            self.assertEqual(sent_poses, ["front", "left"])
            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 2)


if __name__ == "__main__":
    unittest.main()
