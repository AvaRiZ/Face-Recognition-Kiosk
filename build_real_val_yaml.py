from __future__ import annotations

from pathlib import Path


DATASET_ROOT = Path("real_val_dataset")
OUTPUT_YAML = DATASET_ROOT / "real_val_dataset.yaml"
CLASS_NAMES = ["face"]


def main() -> None:
    dataset_root = DATASET_ROOT.resolve()
    images_val_dir = dataset_root / "images" / "val"
    labels_val_dir = dataset_root / "labels" / "val"

    images_val_dir.mkdir(parents=True, exist_ok=True)
    labels_val_dir.mkdir(parents=True, exist_ok=True)

    yaml_text = (
        f"path: {dataset_root.as_posix()}\n"
        "train: images/val\n"
        "val: images/val\n"
        f"names: {CLASS_NAMES}\n"
    )
    OUTPUT_YAML.write_text(yaml_text, encoding="utf-8")

    print("YOLO dataset YAML created.")
    print(f"Dataset root: {dataset_root}")
    print(f"YAML file: {OUTPUT_YAML.resolve()}")
    print()
    print("Note: This validation dataset still needs hand-labeled files in real_val_dataset/labels/val.")


if __name__ == "__main__":
    main()
