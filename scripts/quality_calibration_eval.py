from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import cv2
import numpy as np

from core.config import AppConfig
from services.quality_service import FaceQualityService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline quality calibration evaluator for face-crop datasets."
    )
    parser.add_argument("--input-dir", required=True, help="Directory containing face-crop images.")
    parser.add_argument(
        "--extensions",
        default="jpg,jpeg,png,bmp,webp",
        help="Comma-separated image extensions to include.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively traverse subdirectories under input-dir.",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=0,
        help="Maximum number of images to evaluate (0 means all).",
    )
    parser.add_argument(
        "--detection-confidence",
        type=float,
        default=0.5,
        help="Detection confidence value used during offline scoring.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write JSON summary and per-image compact records.",
    )
    return parser.parse_args()


def collect_image_paths(input_dir: str, extensions: set[str], recursive: bool) -> list[str]:
    paths: list[str] = []
    if recursive:
        for root, _, files in os.walk(input_dir):
            for file_name in files:
                ext = os.path.splitext(file_name)[1].lower().lstrip(".")
                if ext in extensions:
                    paths.append(os.path.join(root, file_name))
    else:
        for file_name in os.listdir(input_dir):
            full_path = os.path.join(input_dir, file_name)
            if not os.path.isfile(full_path):
                continue
            ext = os.path.splitext(file_name)[1].lower().lstrip(".")
            if ext in extensions:
                paths.append(full_path)

    paths.sort()
    return paths


def _quantile_dict(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {}
    percentiles = (5, 10, 25, 50, 75, 90, 95)
    return {f"q{p}": float(np.percentile(values, p)) for p in percentiles}


def summarize_records(records: list[dict], config: AppConfig) -> dict:
    total = len(records)
    if total == 0:
        return {
            "total_evaluated": 0,
            "status_counts": {},
            "note": "No evaluable images were found.",
        }

    scores = np.array([float(item["score"]) for item in records], dtype=np.float32)
    raw_scores = np.array([float(item["raw_score"]) for item in records], dtype=np.float32)

    status_counts = Counter(item["status"] for item in records)
    alignment_source_counts = Counter(item["alignment_source"] for item in records)

    hard_gate_count = sum(1 for item in records if item["hard_gate_triggered"])
    soft_gate_count = sum(1 for item in records if item["soft_gate_applied"])

    weakest_factor_counts: Counter[str] = Counter()
    for item in records:
        for name in item.get("weakest_factors", []):
            weakest_factor_counts[name] += 1

    acceptable_threshold = float(config.face_quality_threshold)
    good_threshold = float(config.face_quality_good_threshold)

    summary = {
        "total_evaluated": total,
        "status_counts": dict(status_counts),
        "alignment_source_counts": dict(alignment_source_counts),
        "hard_gate_rate": float(hard_gate_count / total),
        "soft_gate_rate": float(soft_gate_count / total),
        "score_stats": {
            "mean": float(np.mean(scores)),
            "std": float(np.std(scores)),
            "min": float(np.min(scores)),
            "max": float(np.max(scores)),
            "quantiles": _quantile_dict(scores),
        },
        "raw_score_stats": {
            "mean": float(np.mean(raw_scores)),
            "std": float(np.std(raw_scores)),
            "min": float(np.min(raw_scores)),
            "max": float(np.max(raw_scores)),
            "quantiles": _quantile_dict(raw_scores),
        },
        "threshold_observability": {
            "configured_face_quality_threshold": acceptable_threshold,
            "configured_face_quality_good_threshold": good_threshold,
            "rate_at_or_above_face_quality_threshold": float(np.mean(scores >= acceptable_threshold)),
            "rate_at_or_above_face_quality_good_threshold": float(np.mean(scores >= good_threshold)),
            "distribution_guidance": {
                "q50": float(np.percentile(scores, 50)),
                "q60": float(np.percentile(scores, 60)),
                "q70": float(np.percentile(scores, 70)),
            },
        },
        "weakest_factor_frequency_top5": weakest_factor_counts.most_common(5),
    }
    return summary


def print_summary(summary: dict) -> None:
    if summary.get("total_evaluated", 0) == 0:
        print(summary.get("note", "No data"))
        return

    print("=== Quality Calibration Summary ===")
    print(f"Total evaluated: {summary['total_evaluated']}")
    print(f"Status counts: {summary['status_counts']}")
    print(f"Alignment source counts: {summary['alignment_source_counts']}")
    print(
        f"Hard gate rate: {summary['hard_gate_rate']:.2%} | "
        f"Soft gate rate: {summary['soft_gate_rate']:.2%}"
    )

    score_stats = summary["score_stats"]
    print(
        "Final score mean/std/min/max: "
        f"{score_stats['mean']:.3f} / {score_stats['std']:.3f} / "
        f"{score_stats['min']:.3f} / {score_stats['max']:.3f}"
    )
    print(f"Final score quantiles: {score_stats['quantiles']}")

    threshold_info = summary["threshold_observability"]
    print(
        "Threshold pass rates: "
        f">= face_quality_threshold ({threshold_info['configured_face_quality_threshold']:.2f}) = "
        f"{threshold_info['rate_at_or_above_face_quality_threshold']:.2%}, "
        f">= good_threshold ({threshold_info['configured_face_quality_good_threshold']:.2f}) = "
        f"{threshold_info['rate_at_or_above_face_quality_good_threshold']:.2%}"
    )
    print(f"Distribution guidance (q50/q60/q70): {threshold_info['distribution_guidance']}")
    print(f"Most frequent weak factors: {summary['weakest_factor_frequency_top5']}")


def main() -> int:
    args = parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"Input directory not found: {args.input_dir}")
        return 2

    extensions = {
        ext.strip().lower().lstrip(".")
        for ext in args.extensions.split(",")
        if ext.strip()
    }
    if not extensions:
        print("No valid extensions supplied.")
        return 2

    paths = collect_image_paths(args.input_dir, extensions, args.recursive)
    if args.max_images > 0:
        paths = paths[: args.max_images]

    config = AppConfig()
    config.quality_explainability_enabled = True
    quality_service = FaceQualityService(config)

    records: list[dict] = []
    unreadable: list[str] = []

    for path in paths:
        image = cv2.imread(path)
        if image is None or image.size == 0:
            unreadable.append(path)
            continue

        score, status, debug = quality_service.assess_face_quality(
            image,
            detection_confidence=float(args.detection_confidence),
        )

        weakest = debug.get("explainability", {}).get("weakest_factors", [])
        weakest_names = [str(item.get("name", "")) for item in weakest if isinstance(item, dict)]

        records.append(
            {
                "path": path,
                "score": float(score),
                "status": str(status),
                "raw_score": float(debug.get("raw_quality_score", score)),
                "soft_gated_score": float(debug.get("soft_gated_score", score)),
                "hard_gate_triggered": bool(debug.get("hard_gate_triggered", False)),
                "soft_gate_applied": bool(debug.get("soft_gate_applied", False)),
                "alignment_source": str(debug.get("alignment_source", "unknown")),
                "weakest_factors": weakest_names,
            }
        )

    summary = summarize_records(records, config)

    print(f"Input images discovered: {len(paths)}")
    print(f"Unreadable images skipped: {len(unreadable)}")
    print_summary(summary)

    if args.output_json:
        payload = {
            "input_dir": args.input_dir,
            "recursive": bool(args.recursive),
            "detection_confidence": float(args.detection_confidence),
            "total_input_images": len(paths),
            "unreadable_images": unreadable,
            "summary": summary,
            "records": records,
        }
        with open(args.output_json, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
        print(f"Wrote calibration JSON: {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
