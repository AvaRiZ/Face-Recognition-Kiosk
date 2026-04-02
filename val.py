from __future__ import annotations

import tempfile
from pathlib import Path

from ultralytics import YOLO


MODEL_PATH = "models/yolov12n-face.pt"
DATASET_ROOT = Path("synthetic_val_dataset")
VAL_IMAGES_DIR = DATASET_ROOT / "images" / "val"
VAL_LABELS_DIR = DATASET_ROOT / "labels" / "val"
CLASS_NAMES = ["face"]
IMAGE_SIZE = 640
CONF_THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70]
GOOD_PRECISION_THRESHOLD = 0.70
GOOD_RECALL_THRESHOLD = 0.70
GOOD_F1_THRESHOLD = 0.70
TARGET_RECALL_THRESHOLD = 0.90


def build_temp_data_yaml(dataset_root: Path, val_images_dir: Path, class_names: list[str]) -> str:
    dataset_root = dataset_root.resolve()
    val_images_dir = val_images_dir.resolve()

    val_relative = val_images_dir.relative_to(dataset_root)
    yaml_text = (
        f"path: {dataset_root.as_posix()}\n"
        f"train: {val_relative.as_posix()}\n"
        f"val: {val_relative.as_posix()}\n"
        f"names: {class_names}\n"
    )

    temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    temp_file.write(yaml_text)
    temp_file.flush()
    temp_file.close()
    return temp_file.name


def precision_status(precision: float) -> str:
    return "GOOD" if precision >= GOOD_PRECISION_THRESHOLD else "BAD"


def recall_status(recall: float) -> str:
    return "GOOD" if recall >= GOOD_RECALL_THRESHOLD else "BAD"


def f1_status(f1_score: float) -> str:
    return "GOOD" if f1_score >= GOOD_F1_THRESHOLD else "BAD"


def compute_f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def main() -> None:
    if not VAL_IMAGES_DIR.exists():
        raise FileNotFoundError(
            f"Validation images folder not found: {VAL_IMAGES_DIR.resolve()}\n"
            "Run 'python generate_labels.py' first."
        )
    if not VAL_LABELS_DIR.exists():
        raise FileNotFoundError(
            f"Validation labels folder not found: {VAL_LABELS_DIR.resolve()}\n"
            "Run 'python generate_labels.py' first."
        )

    model = YOLO(MODEL_PATH)
    temp_yaml = build_temp_data_yaml(DATASET_ROOT, VAL_IMAGES_DIR, CLASS_NAMES)
    rows: list[dict[str, float]] = []

    try:
        for conf_threshold in CONF_THRESHOLDS:
            metrics = model.val(data=temp_yaml, imgsz=IMAGE_SIZE, conf=conf_threshold, plots=False)
            result = metrics.results_dict
            precision = float(result["metrics/precision(B)"])
            recall = float(result["metrics/recall(B)"])
            rows.append(
                {
                    "conf": conf_threshold,
                    "precision": precision,
                    "recall": recall,
                    "f1": compute_f1(precision, recall),
                    "map50": float(result["metrics/mAP50(B)"]),
                    "map5095": float(result["metrics/mAP50-95(B)"]),
                }
            )
    finally:
        Path(temp_yaml).unlink(missing_ok=True)

    best_precision_row = max(rows, key=lambda row: (row["precision"], row["map50"], row["recall"]))
    best_balanced_row = max(rows, key=lambda row: (row["f1"], row["precision"], row["recall"], row["map50"]))

    print("Confidence Threshold Sweep")
    for row in rows:
        print(
            f"conf={row['conf']:.2f} | "
            f"precision={row['precision']:.6f} [{precision_status(row['precision'])}] | "
            f"recall={row['recall']:.6f} [{recall_status(row['recall'])}] | "
            f"f1={row['f1']:.6f} [{f1_status(row['f1'])}] | "
            f"mAP50={row['map50']:.6f} | "
            f"mAP50-95={row['map5095']:.6f}"
        )

    print()
    print("Best Precision Result")
    print(f"Best confidence threshold: {best_precision_row['conf']:.2f}")
    print(
        f"Best precision: {best_precision_row['precision']:.6f} "
        f"[{precision_status(best_precision_row['precision'])}]"
    )
    print(
        f"Recall at best precision: {best_precision_row['recall']:.6f} "
        f"[{recall_status(best_precision_row['recall'])}]"
    )
    print(f"F1 at best precision: {best_precision_row['f1']:.6f} [{f1_status(best_precision_row['f1'])}]")
    print(f"mAP50 at best precision: {best_precision_row['map50']:.6f}")
    print(f"mAP50-95 at best precision: {best_precision_row['map5095']:.6f}")
    print()
    print("Best Balanced Result")
    print(f"Recommended confidence threshold: {best_balanced_row['conf']:.2f}")
    print(
        f"Precision at recommended threshold: {best_balanced_row['precision']:.6f} "
        f"[{precision_status(best_balanced_row['precision'])}]"
    )
    print(
        f"Recall at recommended threshold: {best_balanced_row['recall']:.6f} "
        f"[{recall_status(best_balanced_row['recall'])}]"
    )
    print(f"Best F1-score: {best_balanced_row['f1']:.6f} [{f1_status(best_balanced_row['f1'])}]")
    print(f"mAP50 at recommended threshold: {best_balanced_row['map50']:.6f}")
    print(f"mAP50-95 at recommended threshold: {best_balanced_row['map5095']:.6f}")
    print()

    target_recall_rows = [row for row in rows if row["recall"] >= TARGET_RECALL_THRESHOLD]
    print("Target Recall Result")
    print(f"Target recall threshold: {TARGET_RECALL_THRESHOLD:.2f}")
    if target_recall_rows:
        best_target_recall_row = max(
            target_recall_rows,
            key=lambda row: (row["map50"], row["precision"], row["f1"], row["recall"]),
        )
        print(f"Recommended confidence threshold for target recall: {best_target_recall_row['conf']:.2f}")
        print(
            f"Precision at target recall threshold: {best_target_recall_row['precision']:.6f} "
            f"[{precision_status(best_target_recall_row['precision'])}]"
        )
        print(
            f"Recall at target recall threshold: {best_target_recall_row['recall']:.6f} "
            f"[{recall_status(best_target_recall_row['recall'])}]"
        )
        print(f"F1 at target recall threshold: {best_target_recall_row['f1']:.6f} [{f1_status(best_target_recall_row['f1'])}]")
        print(f"Best available mAP50 at target recall threshold: {best_target_recall_row['map50']:.6f}")
        print(f"mAP50-95 at target recall threshold: {best_target_recall_row['map5095']:.6f}")
    else:
        closest_target_recall_row = max(rows, key=lambda row: (row["recall"], row["map50"], row["precision"]))
        print("No tested confidence threshold reached the target recall.")
        print(f"Closest confidence threshold: {closest_target_recall_row['conf']:.2f}")
        print(
            f"Closest precision: {closest_target_recall_row['precision']:.6f} "
            f"[{precision_status(closest_target_recall_row['precision'])}]"
        )
        print(
            f"Closest recall: {closest_target_recall_row['recall']:.6f} "
            f"[{recall_status(closest_target_recall_row['recall'])}]"
        )
        print(f"Closest F1-score: {closest_target_recall_row['f1']:.6f} [{f1_status(closest_target_recall_row['f1'])}]")
        print(f"mAP50 at closest recall: {closest_target_recall_row['map50']:.6f}")
        print(f"mAP50-95 at closest recall: {closest_target_recall_row['map5095']:.6f}")
    print()
    print("Note: These metrics come from synthetic full-image face labels generated from cropped face images.")


if __name__ == "__main__":
    main()
