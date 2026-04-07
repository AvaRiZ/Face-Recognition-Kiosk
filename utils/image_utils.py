from __future__ import annotations

from typing import Tuple

import numpy as np


def crop_face_region(
    image: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    pad_x_ratio: float = 0.0,
    pad_y_ratio: float = 0.0,
) -> tuple[np.ndarray, Tuple[int, int, int, int]] | tuple[None, None]:
    """Clamp, optionally pad, and crop a face region from an image.

    Returns:
        (crop, (x1, y1, x2, y2)) when valid, otherwise (None, None).
    """
    if image is None or getattr(image, "size", 0) == 0:
        return None, None

    h, w = image.shape[:2]
    if x2 <= x1 or y2 <= y1:
        return None, None

    if pad_x_ratio > 0.0 or pad_y_ratio > 0.0:
        box_w = x2 - x1
        box_h = y2 - y1
        pad_x = int(box_w * pad_x_ratio)
        pad_y = int(box_h * pad_y_ratio)
        x1 -= pad_x
        y1 -= pad_y
        x2 += pad_x
        y2 += pad_y

    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(w, x2)
    y2 = min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None, None

    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return None, None
    return crop, (x1, y1, x2, y2)
