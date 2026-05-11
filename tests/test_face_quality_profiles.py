import numpy as np
import unittest

from core.config import AppConfig
from services.quality_service import FaceQualityService


def _checkerboard_face(size=260):
    grid = np.indices((size, size)).sum(axis=0) % 2
    gray = (grid * 255).astype(np.uint8)
    return np.dstack([gray, gray, gray])


class FaceQualityProfileTests(unittest.TestCase):
    def test_default_pose_classifier_has_stable_neutral_band(self):
        config = AppConfig()
        service = FaceQualityService(config)

        landmarks = {
            "left_eye": (80, 100),
            "right_eye": (180, 100),
            "nose": (141, 130),
        }

        self.assertIsNone(service.classify_face_pose(landmarks, width=260, height=260))

    def test_pose_classifier_uses_neutral_band_between_front_and_side(self):
        config = AppConfig()
        config.registration_pose_front_max_yaw_ratio = 0.20
        config.registration_pose_side_min_yaw_ratio = 0.25
        service = FaceQualityService(config)

        landmarks = {
            "left_eye": (80, 100),
            "right_eye": (180, 100),
            "nose": (141, 130),
        }

        self.assertIsNone(service.classify_face_pose(landmarks, width=260, height=260))

    def test_quality_service_uses_context_specific_sharpness_thresholds(self):
        config = AppConfig()
        profiles = config.quality_profiles_to_dict()
        for profile in profiles.values():
            profile["face_quality_threshold"] = 0.2
            profile["face_quality_good_threshold"] = 0.8
            profile["quality_face_area_min"] = 100
            profile["quality_face_area_good"] = 100
            profile["quality_brightness_min"] = 0
            profile["quality_brightness_good_min"] = 0
            profile["quality_brightness_good_max"] = 255
            profile["quality_brightness_max"] = 255
            profile["quality_dynamic_range_min"] = 0
            profile["quality_dynamic_range_good"] = 1
            profile["quality_detection_confidence_min"] = 0
            profile["quality_detection_confidence_good"] = 1

        profiles["entry"]["quality_sharpness_min"] = 1
        profiles["entry"]["quality_sharpness_good"] = 2
        profiles["exit"]["quality_sharpness_min"] = 1_000_000_000
        profiles["exit"]["quality_sharpness_good"] = 1_000_000_001
        config.apply_quality_profiles(profiles)

        service = FaceQualityService(config)
        face_crop = _checkerboard_face()

        entry_score, entry_status, entry_debug = service.assess_face_quality(face_crop, context="entry")
        exit_score, exit_status, exit_debug = service.assess_face_quality(face_crop, context="exit")

        self.assertGreaterEqual(entry_score, config.entry_quality.face_quality_threshold)
        self.assertIn(entry_status, {"Acceptable", "Good"})
        self.assertNotIn("sharpness", entry_debug["failed_checks"])
        self.assertLess(exit_score, config.exit_quality.face_quality_threshold)
        self.assertEqual(exit_status, "Poor")
        self.assertIn("sharpness", exit_debug["failed_checks"])


if __name__ == "__main__":
    unittest.main()
