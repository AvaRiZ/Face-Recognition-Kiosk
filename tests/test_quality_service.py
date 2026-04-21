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
        face = _checkerboard_face()
        landmarks = {
            "left_eye": (45, 55),
            "right_eye": (115, 58),
            "nose": (82, 88),
            "mouth_left": (58, 118),
            "mouth_right": (106, 120),
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
        face = cv2.GaussianBlur(_checkerboard_face(), (31, 31), 0)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertLess(score, self.config.face_quality_threshold)
        self.assertEqual(status, "Poor")
        self.assertIn("sharpness", debug["failed_checks"])

    def test_rejects_bad_brightness(self) -> None:
        face = np.full((160, 160, 3), 245, dtype=np.uint8)

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertLess(score, self.config.face_quality_threshold)
        self.assertEqual(status, "Poor")
        self.assertIn("exposure", debug["failed_checks"])

    def test_uses_landmarks_for_pose_and_truncation(self) -> None:
        face = _checkerboard_face()
        landmarks = {
            "left_eye": (45, 55),
            "right_eye": (115, 58),
            "nose": (82, 88),
            "mouth_left": (58, 118),
            "mouth_right": (106, 120),
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
        face = _checkerboard_face()

        score, status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)

        self.assertEqual(debug["alignment_source"], "unavailable")
        self.assertFalse(debug["landmarks_available"])
        self.assertEqual(debug["quality_degraded_reason"], "landmarks_unavailable")
        self.assertEqual(debug["alignment_score"], 0.0)
        self.assertEqual(debug["pose_score"], 0.0)
        self.assertEqual(status, "Poor")
        self.assertIn("landmarks", debug["failed_checks"])

    def test_unreliable_landmarks_degrade_to_unavailable_instead_of_pose_failure(self) -> None:
        face = _checkerboard_face()
        # Tiny eye span plus large nose offset simulates unstable keypoints.
        landmarks = {
            "left_eye": (78, 55),
            "right_eye": (80, 55),
            "nose": (130, 88),
            "mouth_left": (62, 118),
            "mouth_right": (104, 118),
        }

        score, status, debug = self.service.assess_face_quality(
            face,
            detection_confidence=0.95,
            landmarks=landmarks,
        )

        self.assertEqual(status, "Poor")
        self.assertEqual(debug["alignment_source"], "unavailable")
        self.assertEqual(debug["quality_degraded_reason"], "landmarks_unreliable")
        self.assertIn("landmarks", debug["failed_checks"])
        self.assertNotIn("pose", debug["failed_checks"])
        self.assertLess(score, self.config.face_quality_threshold)

    def test_detect_face_pose_returns_none_for_unreliable_landmarks(self) -> None:
        face = _checkerboard_face()
        landmarks = {
            "left_eye": (80, 55),
            "right_eye": (82, 55),
            "nose": (132, 90),
        }

        pose = self.service.detect_face_pose(face, landmarks=landmarks)

        self.assertIsNone(pose)

    def test_debug_summary_contains_primary_issue_and_component_scores(self) -> None:
        face = cv2.GaussianBlur(_checkerboard_face(), (31, 31), 0)

        _score, _status, debug = self.service.assess_face_quality(face, detection_confidence=0.95)
        summary = self.service.quality_debug_summary(debug)

        self.assertIn("main_issue=too blurry", summary)
        self.assertIn("sharpness=", summary)
        self.assertIn("brightness=", summary)


if __name__ == "__main__":
    unittest.main()
