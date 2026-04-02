from __future__ import annotations

import shutil
from pathlib import Path


SOURCE_ROOT = Path("faces_improved")
OUTPUT_ROOT = Path("synthetic_val_dataset")
IMAGES_DIR = OUTPUT_ROOT / "images" / "val"
LABELS_DIR = OUTPUT_ROOT / "labels" / "val"
VALID_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Single-class YOLO label for "face" covering the full image:
# class_id x_center y_center width height
FULL_IMAGE_FACE_LABEL = "0 0.5 0.5 1.0 1.0\n"


def collect_images(source_root: Path) -> list[Path]:
    return sorted(path for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() in VALID_SUFFIXES)


def build_output_name(image_path: Path, source_root: Path) -> str:
    relative_parent = image_path.relative_to(source_root).parent
    parent_key = "__".join(relative_parent.parts) if relative_parent.parts else "root"
    return f"{parent_key}__{image_path.stem}"


def main() -> None:
    if not SOURCE_ROOT.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE_ROOT.resolve()}")

    image_paths = collect_images(SOURCE_ROOT)
    if not image_paths:
        raise FileNotFoundError(f"No supported image files found in: {SOURCE_ROOT.resolve()}")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    for image_path in image_paths:
        output_stem = build_output_name(image_path, SOURCE_ROOT)
        output_image_path = IMAGES_DIR / f"{output_stem}{image_path.suffix.lower()}"
        output_label_path = LABELS_DIR / f"{output_stem}.txt"

        shutil.copy2(image_path, output_image_path)
        output_label_path.write_text(FULL_IMAGE_FACE_LABEL, encoding="utf-8")
        copied_count += 1

    print("Synthetic YOLO labels created successfully.")
    print(f"Source images scanned: {len(image_paths)}")
    print(f"Images copied to: {IMAGES_DIR.resolve()}")
    print(f"Labels written to: {LABELS_DIR.resolve()}")
    print(f"Total labeled images: {copied_count}")
    print()
    print("Note: These labels mark the entire image as a face.")
    print("They are suitable only because your source images are already cropped face images.")


if __name__ == "__main__":
    main()
