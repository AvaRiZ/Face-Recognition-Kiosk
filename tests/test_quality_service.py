from __future__ import annotations

import unittest

import cv2
import numpy as np

from core.config import AppConfig
from services.quality_service import FaceQualityService


def _checkerboard_face(size: int = 160, block: int = 16) -> np.ndarray:
    ys, xs = np.indices((size, size))
    pattern = ((xs // block) + (ys // block)) % 2
    image = (pattern * 255).astype(np.uint8)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


class FaceQualityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AppConfig()
        self.service = FaceQualityService(self.config)

    def test_accepts_clear_well_exposed_face(self) -> None:
        face = _checkerboard_face(size=320, block=20)
        landmarks = {
            "left_eye": (95, 115),
            "right_eye": (225, 118),
            "nose": (162, 168),
            "mouth_left": (118, 228),
            "mouth_right": (206, 230),
        }

        score, status, debug = self.service.assess_face_quality(
            face,
            detection_confidence=0.95,
            landmarks=landmarks,
        )

        self.assertGreaterEqual(score, self.config.face_quality_threshold)
        self.assertIn(status, {"Acceptable", "Good"})
        self.assertEqual(debug["failed_checks"], [])

    def test_rejects_small_face_crop(self) -> None:
        face = _checkerboard_face(size=40, block=8)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertLess(score, self.config.face_quality_threshold)
        self.assertEqual(status, "Poor")
        self.assertIn("size", debug["failed_checks"])
        self.assertEqual(debug["primary_issue"], "size")
        self.assertEqual(debug["primary_issue_label"], "face too small")

    def test_rejects_blurry_face(self) -> None:
        face = cv2.GaussianBlur(_checkerboard_face(size=320, block=20), (31, 31), 0)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertLess(score, self.config.face_quality_threshold)
        self.assertEqual(status, "Poor")
        self.assertIn("sharpness", debug["failed_checks"])

    def test_rejects_bad_brightness(self) -> None:
        face = np.full((320, 320, 3), 245, dtype=np.uint8)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertLess(score, self.config.face_quality_threshold)
        self.assertEqual(status, "Poor")
        self.assertIn("exposure", debug["failed_checks"])

    def test_uses_landmarks_for_pose_and_truncation(self) -> None:
        face = _checkerboard_face(size=320, block=20)
        landmarks = {
            "left_eye": (95, 115),
            "right_eye": (225, 118),
            "nose": (162, 168),
            "mouth_left": (118, 228),
            "mouth_right": (206, 230),
        }

        score, status, debug = self.service.assess_face_quality(
            face,
            detection_confidence=0.95,
            landmarks=landmarks,
        )

        self.assertGreaterEqual(score, self.config.face_quality_threshold)
        self.assertIn(status, {"Acceptable", "Good"})
        self.assertEqual(debug["alignment_source"], "landmarks")
        self.assertGreater(debug["pose_score"], 0.5)
        self.assertGreater(debug["occlusion_score"], 0.5)

    def test_missing_landmarks_are_not_treated_as_ideal(self) -> None:
        face = _checkerboard_face(size=320, block=20)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertEqual(debug["alignment_source"], "unavailable")
        self.assertFalse(debug["landmarks_available"])
        self.assertEqual(debug["quality_degraded_reason"], "landmarks_unavailable")
        self.assertGreater(debug["alignment_score"], 0.0)
        self.assertGreater(debug["pose_score"], 0.0)
        self.assertIn(status, {"Acceptable", "Good"})
        self.assertNotIn("landmarks", debug["failed_checks"])
        self.assertGreaterEqual(score, self.config.face_quality_threshold)

    def test_unreliable_landmarks_degrade_to_unavailable_instead_of_pose_failure(self) -> None:
        face = _checkerboard_face(size=320, block=20)
        # Tiny eye span plus large nose offset simulates unstable keypoints.
        landmarks = {
            "left_eye": (158, 115),
            "right_eye": (160, 115),
            "nose": (260, 168),
            "mouth_left": (132, 228),
            "mouth_right": (204, 228),
        }

        score, status, debug = self.service.assess_face_quality(
            face,
            detection_confidence=0.95,
            landmarks=landmarks,
        )

        self.assertEqual(debug["alignment_source"], "unavailable")
        self.assertEqual(debug["quality_degraded_reason"], "landmarks_unreliable")
        self.assertNotIn("landmarks", debug["failed_checks"])
        self.assertNotIn("pose", debug["failed_checks"])
        self.assertIn(status, {"Acceptable", "Good"})
        self.assertGreaterEqual(score, self.config.face_quality_threshold)

    def test_detect_face_pose_returns_none_for_unreliable_landmarks(self) -> None:
        face = _checkerboard_face()
        landmarks = {
            "left_eye": (80, 55),
            "right_eye": (82, 55),
            "nose": (132, 90),
        }

        pose = self.service.detect_face_pose(face, landmarks=landmarks)

        self.assertIsNone(pose)

    def test_classify_face_pose_is_deterministic_without_dead_zone(self) -> None:
        # Yaw beyond front threshold is always side-classified for reliable landmarks.
        pose = self.service.classify_face_pose(
            landmarks={
                "left_eye": (60, 70),
                "right_eye": (140, 70),
                "nose": (118, 96),
            },
            width=200,
            height=200,
        )

        self.assertEqual(pose, "right")

    def test_debug_summary_contains_primary_issue_and_component_scores(self) -> None:
        face = cv2.GaussianBlur(_checkerboard_face(size=320, block=20), (31, 31), 0)

        _score, _status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)
        summary = self.service.quality_debug_summary(debug)

        self.assertIn("main_issue=too blurry", summary)
        self.assertIn("sharpness=", summary)
        self.assertIn("brightness=", summary)


if __name__ == "__main__":
    unittest.main()
