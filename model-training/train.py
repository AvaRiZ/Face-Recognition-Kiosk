from __future__ import annotations

import argparse
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ultralytics import SAM, YOLO


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
COCO_PERSON_CLASS_ID = 0
BOOTSTRAP_MODES = ("person", "face")
DEFAULT_PROMPT_CONF = 0.25
DEFAULT_IMG_SIZE = 640
DEFAULT_MAX_DET = 10
DEFAULT_MIN_MASK_AREA = 0.01
FACE_PROMPT_CONF = 0.10
FACE_IMG_SIZE = 1280
FACE_MAX_DET = 50
FACE_MIN_MASK_AREA = 0.0001


@dataclass(frozen=True)
class TrainingPaths:
    root: Path
    images_dir: Path
    yolo_weights: Path
    bootstrap_weights: Path
    sam_weights: Path
    dataset_dir: Path
    runs_dir: Path


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    default_dataset_dir = root / "generated-dataset"
    default_runs_dir = root / "runs"

    parser = argparse.ArgumentParser(
        description=(
            "Auto-generate YOLO labels from unlabeled images using YOLOv8n prompts and "
            "MobileSAM masks, then optionally train a YOLOv8n model."
        )
    )
    parser.add_argument("--images-dir", type=Path, default=root / "Images", help="Folder containing unlabeled source images.")
    parser.add_argument("--yolo-weights", type=Path, default=root / "Yolo-model" / "yolov8n.pt", help="YOLOv8 weights used for training the final model.")
    parser.add_argument("--bootstrap-weights", type=Path, default=None, help="Optional detector weights used only for pseudo-label generation. Use a face detector here for face training.")
    parser.add_argument("--sam-weights", type=Path, default=root / "segmentation-model" / "mobile_sam.pt", help="MobileSAM checkpoint used to refine pseudo-label regions.")
    parser.add_argument("--dataset-dir", type=Path, default=default_dataset_dir, help="Output dataset folder.")
    parser.add_argument("--runs-dir", type=Path, default=default_runs_dir, help="Output folder for Ultralytics runs.")
    parser.add_argument("--bootstrap-mode", choices=BOOTSTRAP_MODES, default="person", help="Prompt source for pseudo-label generation. 'person' uses COCO person detections from yolov8n, 'face' expects a face detector checkpoint in model-training.")
    parser.add_argument("--class-name", default=None, help="Single YOLO class name written into data.yaml. Defaults to the selected bootstrap mode.")
    parser.add_argument("--prompt-class-id", type=int, default=COCO_PERSON_CLASS_ID, help="COCO class id used by YOLOv8n for prompt generation. Default: 0 (person).")
    parser.add_argument("--prompt-conf", type=float, default=DEFAULT_PROMPT_CONF, help="Confidence threshold for prompt detections.")
    parser.add_argument("--imgsz", type=int, default=DEFAULT_IMG_SIZE, help="Training image size.")
    parser.add_argument("--sam-imgsz", type=int, default=1024, help="MobileSAM image size.")
    parser.add_argument("--epochs", type=int, default=50, help="Number of YOLO training epochs.")
    parser.add_argument("--batch", type=int, default=16, help="Training batch size.")
    parser.add_argument("--val-split", type=float, default=0.2, help="Fraction of images reserved for validation.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed used for train/val split.")
    parser.add_argument("--max-det-per-image", type=int, default=DEFAULT_MAX_DET, help="Upper bound for prompt boxes per image.")
    parser.add_argument("--box-padding", type=float, default=0.03, help="Fractional padding applied to YOLO prompt boxes before SAM.")
    parser.add_argument("--min-mask-area", type=float, default=DEFAULT_MIN_MASK_AREA, help="Reject SAM masks smaller than this fraction of the image area.")
    parser.add_argument("--prepare-only", action="store_true", help="Only build the dataset and data.yaml without launching training.")
    parser.add_argument("--overwrite", action="store_true", help="Delete and recreate the generated dataset directory.")
    parser.add_argument("--max-images", type=int, default=None, help="Optional cap on the number of source images to process.")
    parser.add_argument("--device", default=None, help="Optional device override, for example 'cpu' or '0'.")
    parser.add_argument("--workers", type=int, default=2, help="YOLO DataLoader workers.")
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> TrainingPaths:
    root = Path(__file__).resolve().parent
    bootstrap_weights = args.bootstrap_weights
    if bootstrap_weights is None:
        bootstrap_weights = args.yolo_weights
    return TrainingPaths(
        root=root,
        images_dir=args.images_dir.resolve(),
        yolo_weights=args.yolo_weights.resolve(),
        bootstrap_weights=Path(bootstrap_weights).resolve(),
        sam_weights=args.sam_weights.resolve(),
        dataset_dir=args.dataset_dir.resolve(),
        runs_dir=args.runs_dir.resolve(),
    )


def validate_inputs(paths: TrainingPaths) -> None:
    if not paths.images_dir.exists():
        raise FileNotFoundError(f"Images folder not found: {paths.images_dir}")
    if not paths.yolo_weights.exists():
        raise FileNotFoundError(f"YOLO weights not found: {paths.yolo_weights}")
    if not paths.bootstrap_weights.exists():
        raise FileNotFoundError(f"Bootstrap weights not found: {paths.bootstrap_weights}")
    if not paths.sam_weights.exists():
        raise FileNotFoundError(f"MobileSAM weights not found: {paths.sam_weights}")


def collect_images(images_dir: Path, max_images: int | None) -> list[Path]:
    image_paths = sorted(path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if max_images is not None:
        image_paths = image_paths[:max_images]
    if not image_paths:
        raise FileNotFoundError(f"No supported images found in: {images_dir}")
    return image_paths


def reset_dataset_dir(dataset_dir: Path, overwrite: bool) -> None:
    if dataset_dir.exists() and overwrite:
        shutil.rmtree(dataset_dir)
    (dataset_dir / "images" / "train").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "images" / "val").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels" / "train").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels" / "val").mkdir(parents=True, exist_ok=True)


def ensure_runs_dir(runs_dir: Path) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)


def padded_box(box: np.ndarray, width: int, height: int, padding_fraction: float) -> list[float]:
    x1, y1, x2, y2 = box.tolist()
    pad_x = (x2 - x1) * padding_fraction
    pad_y = (y2 - y1) * padding_fraction
    return [
        max(0.0, x1 - pad_x),
        max(0.0, y1 - pad_y),
        min(float(width - 1), x2 + pad_x),
        min(float(height - 1), y2 + pad_y),
    ]


def mask_to_yolo_line(mask: np.ndarray, class_id: int) -> str | None:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return None

    height, width = mask.shape
    x_min = float(xs.min())
    x_max = float(xs.max())
    y_min = float(ys.min())
    y_max = float(ys.max())

    x_center = ((x_min + x_max) / 2.0) / width
    y_center = ((y_min + y_max) / 2.0) / height
    box_width = (x_max - x_min) / width
    box_height = (y_max - y_min) / height

    if box_width <= 0.0 or box_height <= 0.0:
        return None
    return f"{class_id} {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}"


def box_to_yolo_line(box: list[float], image_width: int, image_height: int, class_id: int) -> str | None:
    x1, y1, x2, y2 = box
    box_width = max(0.0, x2 - x1)
    box_height = max(0.0, y2 - y1)
    if box_width <= 0.0 or box_height <= 0.0:
        return None

    x_center = (x1 + x2) / 2.0 / image_width
    y_center = (y1 + y2) / 2.0 / image_height
    width_norm = box_width / image_width
    height_norm = box_height / image_height
    return f"{class_id} {x_center:.6f} {y_center:.6f} {width_norm:.6f} {height_norm:.6f}"


def split_dataset(image_paths: list[Path], val_split: float, seed: int) -> tuple[list[Path], list[Path]]:
    if not 0.0 < val_split < 1.0:
        raise ValueError("--val-split must be between 0 and 1.")

    shuffled = image_paths[:]
    random.Random(seed).shuffle(shuffled)
    val_count = max(1, int(round(len(shuffled) * val_split)))
    if val_count >= len(shuffled):
        val_count = max(1, len(shuffled) - 1)
    val_images = shuffled[:val_count]
    train_images = shuffled[val_count:]

    if not train_images or not val_images:
        raise ValueError("Need at least one training image and one validation image. Add more images or lower --val-split.")
    return train_images, val_images


def write_dataset_yaml(dataset_dir: Path, class_name: str) -> Path:
    yaml_path = dataset_dir / "data.yaml"
    yaml_text = (
        f"path: {dataset_dir.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        f"names: ['{class_name}']\n"
    )
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return yaml_path


def resolve_class_name(args: argparse.Namespace) -> str:
    if args.class_name:
        return args.class_name
    return args.bootstrap_mode


def resolve_prompt_class_id(args: argparse.Namespace) -> int | None:
    if args.bootstrap_mode == "face":
        return None
    return args.prompt_class_id


def validate_bootstrap_configuration(args: argparse.Namespace, paths: TrainingPaths) -> None:
    if args.bootstrap_mode == "person":
        return

    bootstrap_name = paths.bootstrap_weights.name.lower()
    is_plain_coco_weight = bootstrap_name in {"yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt"}
    if is_plain_coco_weight:
        raise ValueError(
            "Face bootstrap mode requires a face detector checkpoint inside model-training. "
            "Add a face model such as 'Yolo-model\\yolov8n-face.pt' and run with "
            "--bootstrap-mode face --bootstrap-weights .\\Yolo-model\\yolov8n-face.pt"
        )


def effective_prompt_conf(args: argparse.Namespace) -> float:
    if args.bootstrap_mode == "face" and args.prompt_conf == DEFAULT_PROMPT_CONF:
        return FACE_PROMPT_CONF
    return args.prompt_conf


def effective_imgsz(args: argparse.Namespace) -> int:
    if args.bootstrap_mode == "face" and args.imgsz == DEFAULT_IMG_SIZE:
        return FACE_IMG_SIZE
    return args.imgsz


def effective_max_det(args: argparse.Namespace) -> int:
    if args.bootstrap_mode == "face" and args.max_det_per_image == DEFAULT_MAX_DET:
        return FACE_MAX_DET
    return args.max_det_per_image


def effective_min_mask_area(args: argparse.Namespace) -> float:
    if args.bootstrap_mode == "face" and args.min_mask_area == DEFAULT_MIN_MASK_AREA:
        return FACE_MIN_MASK_AREA
    return args.min_mask_area


def build_label_map(
    image_paths: list[Path],
    bootstrap_model: YOLO,
    sam_model: SAM,
    args: argparse.Namespace,
    runs_dir: Path,
) -> dict[Path, list[str]]:
    labels_by_image: dict[Path, list[str]] = {}
    mask_area_threshold_cache: dict[tuple[int, int], int] = {}
    prompt_class_id = resolve_prompt_class_id(args)
    prompt_conf = effective_prompt_conf(args)
    prompt_imgsz = effective_imgsz(args)
    max_det = effective_max_det(args)
    min_mask_area_fraction = effective_min_mask_area(args)

    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"Failed to read image: {image_path}")

        height, width = image.shape[:2]
        predict_kwargs = {
            "source": str(image_path),
            "imgsz": prompt_imgsz,
            "conf": prompt_conf,
            "max_det": max_det,
            "save": False,
            "verbose": False,
            "project": str((runs_dir / "bootstrap-detect").resolve()),
            "name": "predict",
            "exist_ok": True,
            "device": args.device,
        }
        if prompt_class_id is not None:
            predict_kwargs["classes"] = [prompt_class_id]
        detection_result = bootstrap_model.predict(**predict_kwargs)[0]

        if detection_result.boxes is None or len(detection_result.boxes) == 0:
            labels_by_image[image_path] = []
            continue

        prompt_boxes = [
            padded_box(box.xyxy[0].cpu().numpy(), width, height, args.box_padding)
            for box in detection_result.boxes
        ]

        segmentation_result = sam_model.predict(
            source=str(image_path),
            bboxes=prompt_boxes,
            imgsz=args.sam_imgsz,
            save=False,
            verbose=False,
            project=str((runs_dir / "bootstrap-segment").resolve()),
            name="predict",
            exist_ok=True,
            device=args.device,
        )[0]

        label_lines: list[str] = []
        if segmentation_result.masks is None:
            labels_by_image[image_path] = deduplicate_labels(
                [line for line in (box_to_yolo_line(box, width, height, class_id=0) for box in prompt_boxes) if line]
            )
            continue

        area_threshold_key = (height, width)
        if area_threshold_key not in mask_area_threshold_cache:
            mask_area_threshold_cache[area_threshold_key] = int(height * width * min_mask_area_fraction)
        min_area_pixels = mask_area_threshold_cache[area_threshold_key]

        prompt_box_fallbacks = [box_to_yolo_line(box, width, height, class_id=0) for box in prompt_boxes]
        mask_tensors = list(segmentation_result.masks.data)
        for index, fallback_line in enumerate(prompt_box_fallbacks):
            if index < len(mask_tensors):
                mask = mask_tensors[index].detach().cpu().numpy().astype(np.uint8)
                if int(mask.sum()) >= min_area_pixels:
                    label_line = mask_to_yolo_line(mask, class_id=0)
                    if label_line is not None:
                        label_lines.append(label_line)
                        continue
            if fallback_line is not None:
                label_lines.append(fallback_line)

        labels_by_image[image_path] = deduplicate_labels(label_lines)

    return labels_by_image


def deduplicate_labels(label_lines: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for line in label_lines:
        if line not in seen:
            seen.add(line)
            ordered.append(line)
    return ordered


def copy_split_files(dataset_dir: Path, split_name: str, image_paths: list[Path], labels_by_image: dict[Path, list[str]]) -> None:
    images_out = dataset_dir / "images" / split_name
    labels_out = dataset_dir / "labels" / split_name

    for image_path in image_paths:
        shutil.copy2(image_path, images_out / image_path.name)
        label_path = labels_out / f"{image_path.stem}.txt"
        label_lines = labels_by_image.get(image_path, [])
        label_text = "\n".join(label_lines)
        if label_text:
            label_text += "\n"
        label_path.write_text(label_text, encoding="utf-8")


def train_yolo(yolo_weights: Path, data_yaml: Path, runs_dir: Path, args: argparse.Namespace) -> None:
    model = YOLO(str(yolo_weights))
    if getattr(model, "task", None) != "detect":
        raise ValueError(
            f"Training weights must be a YOLO detect checkpoint, but got task='{model.task}' from {yolo_weights.name}. "
            "If your face checkpoint is a pose/landmark model, keep using it for --bootstrap-weights and use a detect "
            "model such as 'Yolo-model\\yolov8n.pt' for --yolo-weights."
        )
    model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        project=str((runs_dir / "training").resolve()),
        name="yolov8n-mobile-sam",
        exist_ok=True,
        device=args.device,
    )


def print_summary(
    image_paths: list[Path],
    train_images: list[Path],
    val_images: list[Path],
    labels_by_image: dict[Path, list[str]],
    dataset_dir: Path,
    data_yaml: Path,
    prepare_only: bool,
) -> None:
    labeled_images = sum(1 for lines in labels_by_image.values() if lines)
    empty_images = len(image_paths) - labeled_images
    total_boxes = sum(len(lines) for lines in labels_by_image.values())

    print("Dataset prepared from unlabeled images.")
    print(f"Source images: {len(image_paths)}")
    print(f"Labeled images: {labeled_images}")
    print(f"Images without accepted masks: {empty_images}")
    print(f"Generated boxes: {total_boxes}")
    print(f"Train images: {len(train_images)}")
    print(f"Validation images: {len(val_images)}")
    print(f"Dataset directory: {dataset_dir}")
    print(f"Dataset YAML: {data_yaml}")
    if prepare_only:
        print("Training skipped because --prepare-only was used.")


def main() -> None:
    args = parse_args()
    paths = resolve_paths(args)
    validate_inputs(paths)
    validate_bootstrap_configuration(args, paths)

    image_paths = collect_images(paths.images_dir, args.max_images)
    reset_dataset_dir(paths.dataset_dir, overwrite=args.overwrite)
    ensure_runs_dir(paths.runs_dir)

    bootstrap_model = YOLO(str(paths.bootstrap_weights))
    sam_model = SAM(str(paths.sam_weights))

    labels_by_image = build_label_map(
        image_paths=image_paths,
        bootstrap_model=bootstrap_model,
        sam_model=sam_model,
        args=args,
        runs_dir=paths.runs_dir,
    )

    train_images, val_images = split_dataset(image_paths, args.val_split, args.seed)
    copy_split_files(paths.dataset_dir, "train", train_images, labels_by_image)
    copy_split_files(paths.dataset_dir, "val", val_images, labels_by_image)
    data_yaml = write_dataset_yaml(paths.dataset_dir, resolve_class_name(args))

    print_summary(
        image_paths=image_paths,
        train_images=train_images,
        val_images=val_images,
        labels_by_image=labels_by_image,
        dataset_dir=paths.dataset_dir,
        data_yaml=data_yaml,
        prepare_only=args.prepare_only,
    )

    if not args.prepare_only:
        train_yolo(paths.yolo_weights, data_yaml, paths.runs_dir, args)


if __name__ == "__main__":
    main()
