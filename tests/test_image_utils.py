from __future__ import annotations

import unittest

import numpy as np

from utils.image_utils import crop_face_region


class CropFaceRegionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = np.zeros((100, 120, 3), dtype=np.uint8)

    def test_clamps_negative_coordinates(self) -> None:
        crop, bbox = crop_face_region(self.image, -20, -10, 40, 30)

        self.assertIsNotNone(crop)
        self.assertEqual(bbox, (0, 0, 40, 30))
        self.assertEqual(crop.shape[:2], (30, 40))

    def test_clamps_coordinates_exceeding_bounds(self) -> None:
        crop, bbox = crop_face_region(self.image, 90, 80, 200, 150)

        self.assertIsNotNone(crop)
        self.assertEqual(bbox, (90, 80, 120, 100))
        self.assertEqual(crop.shape[:2], (20, 30))

    def test_returns_none_for_empty_or_invalid_crop(self) -> None:
        crop1, bbox1 = crop_face_region(self.image, 50, 50, 50, 90)
        crop2, bbox2 = crop_face_region(self.image, 130, 10, 160, 30)

        self.assertIsNone(crop1)
        self.assertIsNone(bbox1)
        self.assertIsNone(crop2)
        self.assertIsNone(bbox2)


if __name__ == "__main__":
    unittest.main()
