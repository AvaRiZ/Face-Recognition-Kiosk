import unittest
import tempfile
import time

import numpy as np

from core.config import AppConfig
from core.models import RegistrationSample
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


class _NoopRepository:
    camera_id = 1

    def log_recognition(self, *args, **kwargs):
        raise AssertionError("Recognition logging should not run for a wrong registration pose")

    def update_embeddings(self, *args, **kwargs):
        return None


class _PoseMismatchQualityService:
    def assess_face_quality(self, *args, **kwargs):
        return 1.0, "Good", {"failed_checks": [], "component_scores": {}}

    def detect_face_pose(self, *args, **kwargs):
        return "right"

    def quality_debug_summary(self, debug_info):
        return ""


class _RegistrationSampleApiClient:
    def __init__(self, delay_seconds: float = 0.0):
        self.posts = []
        self.delay_seconds = float(delay_seconds)

    def post_json(self, path, payload):
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        self.posts.append((path, payload))
        return {
            "success": True,
            "duplicate": False,
            "capture_count": len(self.posts),
        }


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

    def test_worker_repository_flushes_registration_sample_after_enqueue(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            api_client = _RegistrationSampleApiClient()
            queue = DurableOutboundQueue(queue_dir)
            repository = WorkerApiRepository(
                api_client=api_client,
                outbound_queue=queue,
                station_id="entry-station-1",
                camera_id=1,
            )

            entry_id = repository.enqueue_registration_sample(
                sample_id="sample-1",
                session_id="session-1",
                pose="front",
                quality=0.95,
                face_crop=np.zeros((24, 24, 3), dtype=np.uint8),
                embeddings={"ArcFace": [np.ones(4, dtype=np.float32)]},
            )

            self.assertIsNotNone(entry_id)
            deadline = time.time() + 2.0
            while queue.count_pending("registration_sample", session_id="session-1") and time.time() < deadline:
                time.sleep(0.01)

            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 0)
            self.assertEqual(len(api_client.posts), 1)
            self.assertEqual(api_client.posts[0][0], "/api/internal/registrations/samples")
            self.assertEqual(api_client.posts[0][1]["sample_id"], "sample-1")

    def test_worker_repository_registration_enqueue_does_not_wait_for_slow_api(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            api_client = _RegistrationSampleApiClient(delay_seconds=0.25)
            queue = DurableOutboundQueue(queue_dir)
            repository = WorkerApiRepository(
                api_client=api_client,
                outbound_queue=queue,
                station_id="entry-station-1",
                camera_id=1,
            )

            started_at = time.perf_counter()
            entry_id = repository.enqueue_registration_sample(
                sample_id="sample-1",
                session_id="session-1",
                pose="front",
                quality=0.95,
                face_crop=np.zeros((24, 24, 3), dtype=np.uint8),
                embeddings={"ArcFace": [np.ones(4, dtype=np.float32)]},
            )
            elapsed = time.perf_counter() - started_at

            self.assertIsNotNone(entry_id)
            self.assertLess(elapsed, 0.15)

            deadline = time.time() + 2.0
            while queue.count_pending("registration_sample", session_id="session-1") and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 0)

    def test_registration_queue_preserves_session_order_after_failure(self):
        with tempfile.TemporaryDirectory() as queue_dir:
            queue = DurableOutboundQueue(queue_dir)
            queue.enqueue("registration_sample", {"session_id": "session-1", "pose": "front"})
            time.sleep(0.002)
            queue.enqueue("registration_sample", {"session_id": "session-1", "pose": "left"})
            sent_poses = []

            def sender(entry):
                sent_poses.append(entry["payload"]["pose"])
                return False

            sent, remaining = queue.drain_once(sender)

            self.assertEqual(sent, 0)
            self.assertEqual(remaining, 2)
            self.assertEqual(sent_poses, ["front"])
            self.assertEqual(queue.count_pending("registration_sample", session_id="session-1"), 2)


if __name__ == "__main__":
    unittest.main()
