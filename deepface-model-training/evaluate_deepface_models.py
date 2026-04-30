from __future__ import annotations

import argparse
import csv
import itertools
import json
import pickle
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class ImageRecord:
    identity: str
    path: Path


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Evaluate ArcFace/Facenet confidence thresholds on the augmented folder dataset."
    )
    parser.add_argument("--config", type=Path, default=root / "deepface_experiment_config.json")
    parser.add_argument("--images-dir", type=Path, default=None)
    parser.add_argument("--cache-path", type=Path, default=root / "deepface_embedding_cache.pkl")
    parser.add_argument("--max-identities", type=int, default=None)
    parser.add_argument("--originals-only", action="store_true")
    parser.add_argument("--max-negative-pairs", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--results-dir", type=Path, default=root / "results")
    return parser.parse_args()


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_images_dir(args: argparse.Namespace, config: dict) -> Path:
    if args.images_dir is not None:
        return args.images_dir.resolve()
    configured = Path(config["dataset"]["images_dir"])
    if configured.is_absolute():
        return configured
    return (args.config.resolve().parent / configured).resolve()


def collect_records(images_dir: Path, max_identities: int | None, originals_only: bool) -> list[ImageRecord]:
    identity_dirs = sorted(path for path in images_dir.iterdir() if path.is_dir())
    if max_identities is not None:
        identity_dirs = identity_dirs[:max_identities]

    records: list[ImageRecord] = []
    for identity_dir in identity_dirs:
        for path in sorted(identity_dir.iterdir()):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if originals_only and path.stem.startswith("aug_"):
                continue
            records.append(ImageRecord(identity=identity_dir.name, path=path.resolve()))

    if not records:
        raise FileNotFoundError(f"No images found in: {images_dir}")
    return records


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return pickle.loads(path.read_bytes())
    except Exception:
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.write_bytes(pickle.dumps(cache))


def cache_key(record: ImageRecord, model_name: str, model_config: dict) -> tuple:
    stat = record.path.stat()
    return (
        str(record.path),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        model_name,
        model_config.get("detector_backend", "skip"),
        bool(model_config.get("align", False)),
        model_config.get("normalization", "base"),
    )


def extract_embedding(record: ImageRecord, model_name: str, model_config: dict, deepface) -> np.ndarray:
    image_bgr = cv2.imread(str(record.path))
    if image_bgr is None:
        raise RuntimeError(f"Failed to read image: {record.path}")

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    embedding_obj = deepface.represent(
        img_path=image_rgb,
        model_name=model_name,
        enforce_detection=False,
        detector_backend=model_config.get("detector_backend", "skip"),
        align=bool(model_config.get("align", False)),
        normalization=model_config.get("normalization", "base"),
    )
    vector = np.asarray(embedding_obj[0]["embedding"], dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector


def build_embeddings(records: list[ImageRecord], config: dict, cache_path: Path) -> dict[str, dict[Path, np.ndarray]]:
    from deepface import DeepFace

    cache = load_cache(cache_path)
    embeddings: dict[str, dict[Path, np.ndarray]] = {}

    for model_name, model_config in config["models"].items():
        embeddings[model_name] = {}
        for index, record in enumerate(records, start=1):
            key = cache_key(record, model_name, model_config)
            vector = cache.get(key)
            if vector is None:
                vector = extract_embedding(record, model_name, model_config, DeepFace)
                cache[key] = vector
            embeddings[model_name][record.path] = np.asarray(vector, dtype=np.float32)
            if index % 25 == 0:
                print(f"[{model_name}] embeddings: {index}/{len(records)}")

    save_cache(cache_path, cache)
    return embeddings


def build_pairs(records: list[ImageRecord], max_negative_pairs: int, seed: int) -> list[tuple[ImageRecord, ImageRecord, bool]]:
    by_identity: dict[str, list[ImageRecord]] = {}
    for record in records:
        by_identity.setdefault(record.identity, []).append(record)

    pairs: list[tuple[ImageRecord, ImageRecord, bool]] = []
    for identity_records in by_identity.values():
        pairs.extend((a, b, True) for a, b in itertools.combinations(identity_records, 2))

    rng = random.Random(seed)
    identities = sorted(by_identity)
    negative_pairs: set[tuple[Path, Path]] = set()
    while len(negative_pairs) < max_negative_pairs and len(identities) > 1:
        left_id, right_id = rng.sample(identities, 2)
        left = rng.choice(by_identity[left_id])
        right = rng.choice(by_identity[right_id])
        key = tuple(sorted((left.path, right.path), key=str))
        negative_pairs.add(key)

    path_to_record = {record.path: record for record in records}
    pairs.extend((path_to_record[left], path_to_record[right], False) for left, right in sorted(negative_pairs, key=lambda item: (str(item[0]), str(item[1]))))
    return pairs


def metrics_for_predictions(labels: list[bool], predictions: list[bool]) -> dict[str, float]:
    tp = sum(1 for label, pred in zip(labels, predictions) if label and pred)
    tn = sum(1 for label, pred in zip(labels, predictions) if not label and not pred)
    fp = sum(1 for label, pred in zip(labels, predictions) if not label and pred)
    fn = sum(1 for label, pred in zip(labels, predictions) if label and not pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / max(1, len(labels))
    return {"accuracy": accuracy, "precision": precision, "recall": recall, "f1": f1, "fp": float(fp), "fn": float(fn)}


def cosine_confidence(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def evaluate_single_models(config: dict, embeddings: dict[str, dict[Path, np.ndarray]], pairs) -> list[dict[str, float | str]]:
    labels = [is_positive for _, _, is_positive in pairs]
    rows: list[dict[str, float | str]] = []
    for model_name, model_config in config["models"].items():
        scores = [cosine_confidence(embeddings[model_name][left.path], embeddings[model_name][right.path]) for left, right, _ in pairs]
        best = None
        for threshold in model_config["threshold_candidates"]:
            predictions = [score >= float(threshold) for score in scores]
            metrics = metrics_for_predictions(labels, predictions)
            row = {"model": model_name, "threshold": float(threshold), **metrics}
            rows.append(row)
            if best is None or row["f1"] > best["f1"]:
                best = row
        print(
            f"{model_name}: best_threshold={best['threshold']:.2f}, "
            f"f1={best['f1']:.3f}, precision={best['precision']:.3f}, "
            f"recall={best['recall']:.3f}, accuracy={best['accuracy']:.3f}, "
            f"fp={int(best['fp'])}, fn={int(best['fn'])}"
        )
    return rows


def evaluate_ensemble(config: dict, embeddings: dict[str, dict[Path, np.ndarray]], pairs) -> list[dict[str, float | str]]:
    labels = [is_positive for _, _, is_positive in pairs]
    rows: list[dict[str, float | str]] = []
    for thresholds in config["ensemble"]["threshold_pairs"]:
        predictions = []
        for left, right, _ in pairs:
            passed = True
            for model_name, threshold in thresholds.items():
                score = cosine_confidence(embeddings[model_name][left.path], embeddings[model_name][right.path])
                passed = passed and score >= float(threshold)
            predictions.append(passed)
        metrics = metrics_for_predictions(labels, predictions)
        joined = ", ".join(f"{model}={threshold:.2f}" for model, threshold in thresholds.items())
        row = {
            "model": "ArcFace + Facenet",
            "threshold": joined,
            **metrics,
        }
        rows.append(row)
        print(
            f"Ensemble ({joined}): f1={metrics['f1']:.3f}, "
            f"precision={metrics['precision']:.3f}, recall={metrics['recall']:.3f}, "
            f"accuracy={metrics['accuracy']:.3f}, fp={int(metrics['fp'])}, fn={int(metrics['fn'])}"
        )
    return rows


def best_rows(rows: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    best_by_model: dict[str, dict[str, float | str]] = {}
    for row in rows:
        model = str(row["model"])
        if model not in best_by_model or float(row["f1"]) > float(best_by_model[model]["f1"]):
            best_by_model[model] = row
    return list(best_by_model.values())


def format_float(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return f"{float(value):.3f}"


def markdown_table(rows: list[dict[str, float | str]]) -> str:
    header = "| Model | Threshold | Accuracy | Precision | Recall | F1-score | FP | FN |"
    separator = "|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, separator]
    for row in rows:
        lines.append(
            "| "
            f"{row['model']} | {row['threshold']} | {format_float(row['accuracy'])} | "
            f"{format_float(row['precision'])} | {format_float(row['recall'])} | "
            f"{format_float(row['f1'])} | {int(float(row['fp']))} | {int(float(row['fn']))} |"
        )
    return "\n".join(lines)


def write_results(
    results_dir: Path,
    records: list[ImageRecord],
    pairs,
    single_rows: list[dict[str, float | str]],
    ensemble_rows: list[dict[str, float | str]],
) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "deepface_tuning_results.csv"
    md_path = results_dir / "deepface_tuning_results.md"
    all_rows = single_rows + ensemble_rows

    fieldnames = ["model", "threshold", "accuracy", "precision", "recall", "f1", "fp", "fn"]
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    positive_pairs = sum(1 for _, _, is_positive in pairs if is_positive)
    negative_pairs = len(pairs) - positive_pairs
    best_summary = best_rows(single_rows) + best_rows(ensemble_rows)
    markdown = (
        "# DeepFace Tuning Results\n\n"
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"
        "## Dataset\n\n"
        f"- Images: {len(records)}\n"
        f"- Identities: {len({record.identity for record in records})}\n"
        f"- Positive pairs: {positive_pairs}\n"
        f"- Negative pairs: {negative_pairs}\n\n"
        "## Best Results\n\n"
        f"{markdown_table(best_summary)}\n\n"
        "## Single-Model Threshold Sweep\n\n"
        f"{markdown_table(single_rows)}\n\n"
        "## Ensemble Threshold Sweep\n\n"
        f"{markdown_table(ensemble_rows)}\n"
    )
    md_path.write_text(markdown, encoding="utf-8")
    return md_path, csv_path


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    images_dir = resolve_images_dir(args, config)
    records = collect_records(images_dir, args.max_identities, args.originals_only)
    pairs = build_pairs(records, args.max_negative_pairs, args.seed)

    print(f"Images: {len(records)}")
    print(f"Pairs: {len(pairs)}")
    embeddings = build_embeddings(records, config, args.cache_path.resolve())
    single_rows = evaluate_single_models(config, embeddings, pairs)
    ensemble_rows = evaluate_ensemble(config, embeddings, pairs)
    md_path, csv_path = write_results(args.results_dir.resolve(), records, pairs, single_rows, ensemble_rows)
    print(f"Markdown results: {md_path}")
    print(f"CSV results: {csv_path}")


if __name__ == "__main__":
    main()
