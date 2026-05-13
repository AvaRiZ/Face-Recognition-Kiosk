from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_IMAGE_DIR = REPO_ROOT / "Static-image-benchmark" / "detector_dataset" / "images" / "train"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "Static-image-benchmark"

STAGE_LABELS = {
    "face_detection_yolov8_ms": "Face Detection (YOLOv8)",
    "arcface_embedding_ms": "ArcFace Embedding Generation",
    "facenet_embedding_ms": "FaceNet Embedding Generation",
    "database_query_comparison_ms": "Database Query and Comparison",
    "total_end_to_end_ms": "Total End-to-End Recognition Time",
}


@dataclass
class BenchmarkRecord:
    image_path: str
    face_detection_yolov8_ms: float
    arcface_embedding_ms: float
    facenet_embedding_ms: float
    database_query_comparison_ms: float
    total_end_to_end_ms: float
    matched_user_id: int | None
    matched_name: str | None
    confidence: float | None


def _load_env_file_if_present(file_path: Path) -> None:
    if not file_path.exists():
        return

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _ensure_realtime_importable_for_benchmark() -> None:
    try:
        __import__("app.realtime")
        return
    except ModuleNotFoundError as exc:
        if exc.name != "flask_socketio":
            raise

    realtime_stub = types.ModuleType("app.realtime")
    realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
    realtime_stub.emit_capacity_threshold_alert = lambda *args, **kwargs: None
    realtime_stub.emit_unrecognized_detection = lambda *args, **kwargs: None
    sys.modules.setdefault("app.realtime", realtime_stub)


def discover_images(image_dir: Path, limit: int | None = None) -> list[Path]:
    images = [
        path
        for path in sorted(image_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if limit is not None:
        return images[: max(0, int(limit))]
    return images


def summarize_benchmark(
    records: Iterable[BenchmarkRecord],
    *,
    scanned_count: int,
    skipped: dict[str, int] | None = None,
    users_loaded: int = 0,
    embeddings_loaded: int = 0,
) -> dict:
    rows = list(records)
    averages = {}
    for key, label in STAGE_LABELS.items():
        values = [float(getattr(row, key)) for row in rows]
        averages[key] = {
            "label": label,
            "average_ms": statistics.fmean(values) if values else None,
            "sample_count": len(values),
        }

    return {
        "scanned_images": int(scanned_count),
        "processed_images": len(rows),
        "skipped_images": int(sum((skipped or {}).values())),
        "skip_reasons": dict(skipped or {}),
        "users_loaded": int(users_loaded),
        "embeddings_loaded": int(embeddings_loaded),
        "averages": averages,
        "records": [asdict(row) for row in rows],
        "note": (
            "Profiles are loaded once from PostgreSQL; per-image matching uses "
            "the kiosk's in-memory vector index."
        ),
    }


def _print_summary(summary: dict) -> None:
    print("\nStatic Image Recognition Benchmark")
    print("=" * 41)
    print(f"Images scanned:   {summary['scanned_images']}")
    print(f"Images processed: {summary['processed_images']}")
    print(f"Images skipped:   {summary['skipped_images']}")
    if summary["skip_reasons"]:
        print("Skip reasons:     " + ", ".join(f"{k}={v}" for k, v in summary["skip_reasons"].items()))
    print(f"Users loaded:     {summary['users_loaded']}")
    print(f"Embeddings loaded:{summary['embeddings_loaded']}")
    print()
    print(f"{'Processing Stage':<42} Average Time")
    print(f"{'-' * 42} {'-' * 14}")
    for key in STAGE_LABELS:
        row = summary["averages"][key]
        average_ms = row["average_ms"]
        display = "n/a" if average_ms is None else f"{average_ms:.2f} ms"
        print(f"{row['label']:<42} {display:>14}")
    print()
    print("Note: " + summary["note"])


def _write_outputs(summary: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_results.json"
    csv_path = output_dir / "benchmark_results.csv"

    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")

    fieldnames = [
        "row_type",
        "processing_stage",
        "average_ms",
        "sample_count",
        "image_path",
        "face_detection_yolov8_ms",
        "arcface_embedding_ms",
        "facenet_embedding_ms",
        "database_query_comparison_ms",
        "total_end_to_end_ms",
        "matched_user_id",
        "matched_name",
        "confidence",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for key in STAGE_LABELS:
            average_row = summary["averages"][key]
            writer.writerow(
                {
                    "row_type": "average",
                    "processing_stage": average_row["label"],
                    "average_ms": average_row["average_ms"],
                    "sample_count": average_row["sample_count"],
                }
            )
        for record in summary["records"]:
            row = {key: record.get(key) for key in fieldnames}
            row["row_type"] = "record"
            writer.writerow(row)

    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")


def _best_detection_box(results) -> tuple[int, int, int, int] | None:
    candidates: list[tuple[float, float, tuple[int, int, int, int]]] = []
    for result in results or []:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            try:
                xyxy = box.xyxy[0]
                if hasattr(xyxy, "detach"):
                    xyxy = xyxy.detach().cpu().numpy()
                elif hasattr(xyxy, "cpu"):
                    xyxy = xyxy.cpu().numpy()
                x1, y1, x2, y2 = [int(round(float(value))) for value in xyxy[:4]]
                if x2 <= x1 or y2 <= y1:
                    continue
                confidence = 0.0
                if getattr(box, "conf", None) is not None:
                    confidence = float(box.conf[0])
                area = float((x2 - x1) * (y2 - y1))
                candidates.append((confidence, area, (x1, y1, x2, y2)))
            except Exception:
                continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _resolve_model_timing(timings: dict[str, float], model_name: str) -> float | None:
    target = model_name.strip().casefold()
    for key, value in timings.items():
        if key.strip().casefold() == target:
            return float(value)
    return None


def run_benchmark(args: argparse.Namespace) -> dict:
    from ultralytics import YOLO

    from core.config import AppConfig, resolve_yolo_device
    from core.state import AppStateManager
    _ensure_realtime_importable_for_benchmark()
    from database.repository import UserRepository
    from db import is_postgres_target, resolve_database_target
    from services.embedding_service import EmbeddingService, count_embeddings
    from services.recognition_service import FaceRecognitionService
    from utils.image_utils import crop_face_region

    _load_env_file_if_present(REPO_ROOT / ".env.local")

    config = AppConfig()
    database_target = resolve_database_target(config.db_path)
    if not is_postgres_target(database_target):
        raise RuntimeError(
            "DATABASE_URL is required for live database comparison timing. "
            "Set it to a PostgreSQL DSN or add it to .env.local."
        )

    repository = UserRepository(
        config.db_path,
        stale_inside_reentry_seconds=int(getattr(config, "stale_inside_reentry_seconds", 30 * 60)),
    )
    users = repository.get_all_users()
    embeddings_loaded = sum(count_embeddings(user.embeddings) for user in users)
    if not users or embeddings_loaded <= 0:
        raise RuntimeError(
            "No live users with embeddings were loaded from PostgreSQL. "
            "Register profiles before running the benchmark."
        )

    state = AppStateManager(config)
    state.load_users(users)

    yolo_device = args.device or resolve_yolo_device(config.torch_device_index)
    print(f"Loading YOLO model: {config.model_path}")
    yolo_model = YOLO(config.model_path)
    try:
        yolo_model.to(yolo_device)
    except Exception as exc:
        print(f"[WARN] YOLO device warning: {exc}")
    print("Warming up YOLO model...")
    yolo_model.predict(
        source=np.zeros((int(getattr(config, "yolo_inference_imgsz", 960)), int(getattr(config, "yolo_inference_imgsz", 960)), 3), dtype=np.uint8),
        imgsz=int(getattr(config, "yolo_inference_imgsz", 960)),
        conf=float(getattr(config, "yolo_detection_confidence", 0.20)),
        device=yolo_device,
        verbose=False,
    )

    embedding_service = EmbeddingService(config)
    print("Warming up embedding models...")
    embedding_service.warm_up_models()

    recognition_service = FaceRecognitionService(
        config=config,
        state=state,
        repository=repository,
        embedding_service=embedding_service,
    )

    image_paths = discover_images(Path(args.image_dir), limit=args.limit)
    if not image_paths:
        raise RuntimeError(f"No benchmark images found in {args.image_dir}")

    print(f"Running benchmark on {len(image_paths)} image(s)...")
    records: list[BenchmarkRecord] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for index, image_path in enumerate(image_paths, start=1):
        total_start = time.perf_counter()
        image = cv2.imread(str(image_path))
        if image is None:
            skip("image_unreadable")
            continue

        detection_start = time.perf_counter()
        results = yolo_model.predict(
            source=image,
            imgsz=int(getattr(config, "yolo_inference_imgsz", 960)),
            conf=float(getattr(config, "yolo_detection_confidence", 0.20)),
            device=yolo_device,
            verbose=False,
        )
        detection_ms = (time.perf_counter() - detection_start) * 1000.0

        bbox = _best_detection_box(results)
        if bbox is None:
            skip("no_face_detected")
            continue

        face_crop, _clamped_bbox = crop_face_region(image, *bbox)
        if face_crop is None:
            skip("invalid_face_crop")
            continue

        model_timings: dict[str, float] = {}

        def record_embedding_timing(model_name: str, elapsed_ms: float) -> None:
            model_timings[str(model_name)] = float(elapsed_ms)

        embeddings = embedding_service.extract_embedding_ensemble(
            face_crop,
            timing_callback=record_embedding_timing,
        )
        arcface_ms = _resolve_model_timing(model_timings, config.primary_model)
        facenet_ms = _resolve_model_timing(model_timings, config.secondary_model)
        if not embeddings or arcface_ms is None or facenet_ms is None:
            skip("embedding_failed")
            continue

        comparison_start = time.perf_counter()
        match = recognition_service.find_best_match(embeddings)
        comparison_ms = (time.perf_counter() - comparison_start) * 1000.0
        total_ms = (time.perf_counter() - total_start) * 1000.0

        records.append(
            BenchmarkRecord(
                image_path=str(image_path),
                face_detection_yolov8_ms=detection_ms,
                arcface_embedding_ms=arcface_ms,
                facenet_embedding_ms=facenet_ms,
                database_query_comparison_ms=comparison_ms,
                total_end_to_end_ms=total_ms,
                matched_user_id=int(match.user_id) if match else None,
                matched_name=match.user.name if match else None,
                confidence=float(match.confidence) if match else None,
            )
        )
        print(f"[{index}/{len(image_paths)}] processed {image_path.name}")

    summary = summarize_benchmark(
        records,
        scanned_count=len(image_paths),
        skipped=skipped,
        users_loaded=len(users),
        embeddings_loaded=embeddings_loaded,
    )
    _print_summary(summary)
    output_dir = Path(args.output_dir)
    _write_outputs(summary, output_dir)
    if not args.skip_visuals:
        from scripts.render_benchmark_visuals import render_visuals

        svg_path, png_path = render_visuals(output_dir / "benchmark_results.json", output_dir)
        print(f"Saved SVG figure: {svg_path}")
        print(f"Saved PNG figure: {png_path}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark static images through the kiosk recognition pipeline.",
    )
    parser.add_argument("--image-dir", default=str(DEFAULT_IMAGE_DIR), help="Directory of benchmark images.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of images to process.")
    parser.add_argument("--device", default=None, help="YOLO device override, e.g. cpu or cuda:0.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON and CSV results.")
    parser.add_argument("--skip-visuals", action="store_true", help="Do not render SVG/PNG proof figures.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_benchmark(args)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
