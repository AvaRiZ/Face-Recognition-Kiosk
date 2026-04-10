from __future__ import annotations
from pathlib import Path

import cv2
import numpy as np

from core.config import AppConfig


FULL_IMAGE_FACE_LABEL = "0 0.5 0.5 1.0 1.0\n"
README_TEXT = """Real validation dataset scaffold

Use this dataset for hand-labeled validation on real frames.

Expected structure:
- images/val/*.jpg
- labels/val/*.txt

Each label file should use YOLO detection format:
<class_id> <x_center> <y_center> <width> <height>

For this project, use class_id 0 for face.
"""


class DetectorDatasetService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.detector_root = Path(config.detector_dataset_dir)
        self.train_images_dir = self.detector_root / "images" / config.detector_train_split
        self.train_labels_dir = self.detector_root / "labels" / config.detector_train_split
        self.real_val_root = Path(config.real_val_dataset_dir)
        self.real_val_images_dir = self.real_val_root / "images" / "val"
        self.real_val_labels_dir = self.real_val_root / "labels" / "val"

    def ensure_structure(self) -> None:
        self.train_images_dir.mkdir(parents=True, exist_ok=True)
        self.train_labels_dir.mkdir(parents=True, exist_ok=True)
        self.real_val_images_dir.mkdir(parents=True, exist_ok=True)
        self.real_val_labels_dir.mkdir(parents=True, exist_ok=True)

        readme_path = self.real_val_root / "README.txt"
        if not readme_path.exists():
            readme_path.write_text(README_TEXT, encoding="utf-8")

    def save_recognized_face_crop(self, face_crop: np.ndarray, sr_code: str, timestamp: int) -> tuple[str, str] | None:
        if face_crop is None or getattr(face_crop, "size", 0) == 0:
            return None

        safe_sr_code = self._sanitize_name(sr_code)
        stem = f"{safe_sr_code}_{timestamp}"
        image_path = self.train_images_dir / f"{stem}.jpg"
        label_path = self.train_labels_dir / f"{stem}.txt"

        if not cv2.imwrite(str(image_path), face_crop):
            return None
        label_path.write_text(FULL_IMAGE_FACE_LABEL, encoding="utf-8")
        return str(image_path), str(label_path)

    def count_real_val_frames(self) -> int:
        return sum(1 for path in self.real_val_images_dir.iterdir() if path.is_file()) if self.real_val_images_dir.exists() else 0

    def save_real_val_frame(self, frame: np.ndarray, frame_index: int) -> str | None:
        if frame is None or getattr(frame, "size", 0) == 0:
            return None

        output_path = self.real_val_images_dir / f"frame_{frame_index:06d}.jpg"
        if not cv2.imwrite(str(output_path), frame):
            return None
        return str(output_path)

    @staticmethod
    def _sanitize_name(value: str) -> str:
        filtered = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
        return filtered or "unknown"
