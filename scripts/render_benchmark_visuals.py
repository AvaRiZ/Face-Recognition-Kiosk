from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_PATH = REPO_ROOT / "Static-image-benchmark" / "benchmark_results.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "Static-image-benchmark"

STAGE_KEYS = [
    "face_detection_yolov8_ms",
    "arcface_embedding_ms",
    "facenet_embedding_ms",
    "database_query_comparison_ms",
    "total_end_to_end_ms",
]


def load_summary(results_path: Path) -> dict:
    with results_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def stage_rows(summary: dict) -> list[dict]:
    averages = summary.get("averages") or {}
    rows = []
    for key in STAGE_KEYS:
        row = averages.get(key) or {}
        average_ms = row.get("average_ms")
        if average_ms is None:
            continue
        rows.append(
            {
                "key": key,
                "label": str(row.get("label") or key),
                "average_ms": float(average_ms),
                "sample_count": int(row.get("sample_count") or 0),
            }
        )
    return rows


def _fmt_ms(value: float) -> str:
    return f"{float(value):.2f} ms"


def render_svg(summary: dict, output_path: Path) -> None:
    rows = stage_rows(summary)
    if not rows:
        raise ValueError("No average benchmark rows found.")

    width = 1600
    height = 1000
    margin_x = 92
    title_y = 90
    chart_x = 520
    chart_y = 330
    chart_w = 840
    bar_h = 56
    row_gap = 42
    max_value = max(row["average_ms"] for row in rows)

    colors = {
        "face_detection_yolov8_ms": "#0072BB",
        "arcface_embedding_ms": "#2E7D32",
        "facenet_embedding_ms": "#8A4F00",
        "database_query_comparison_ms": "#6C757D",
        "total_end_to_end_ms": "#ED1B2F",
    }

    def text(x, y, content, size=32, weight=400, fill="#1F2933", anchor="start"):
        return (
            f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{html.escape(str(content))}</text>'
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1600" height="1000" fill="#FFFFFF"/>',
        f'<rect x="0" y="0" width="{width}" height="18" fill="#0072BB"/>',
        text(margin_x, title_y, "Static Image Recognition Benchmark", 46, 700),
        text(
            margin_x,
            title_y + 52,
            "Average processing time per recognition stage, measured from saved test images",
            25,
            400,
            "#52616B",
        ),
    ]

    metric_cards = [
        ("Images processed", summary.get("processed_images", 0)),
        ("Images skipped", summary.get("skipped_images", 0)),
        ("Users loaded", summary.get("users_loaded", 0)),
        ("Embeddings loaded", summary.get("embeddings_loaded", 0)),
    ]
    card_y = 190
    card_w = 330
    card_h = 92
    for idx, (label, value) in enumerate(metric_cards):
        x = margin_x + idx * (card_w + 28)
        parts.append(f'<rect x="{x}" y="{card_y}" width="{card_w}" height="{card_h}" rx="10" fill="#F4F7FA" stroke="#D7DEE6"/>')
        parts.append(text(x + 24, card_y + 38, label, 21, 600, "#52616B"))
        parts.append(text(x + 24, card_y + 76, f"{value:,}" if isinstance(value, int) else value, 34, 700, "#1F2933"))

    parts.append(text(margin_x, chart_y - 42, "Processing Stage", 24, 700, "#1F2933"))
    parts.append(text(chart_x, chart_y - 42, "Average Time", 24, 700, "#1F2933"))
    parts.append(f'<line x1="{margin_x}" y1="{chart_y - 18}" x2="{width - margin_x}" y2="{chart_y - 18}" stroke="#D7DEE6" stroke-width="2"/>')

    for idx, row in enumerate(rows):
        y = chart_y + idx * (bar_h + row_gap)
        bar_w = max(4, (row["average_ms"] / max_value) * chart_w)
        fill = colors.get(row["key"], "#0072BB")
        value_label = _fmt_ms(row["average_ms"])
        parts.append(text(margin_x, y + 36, row["label"], 25, 600, "#1F2933"))
        parts.append(f'<rect x="{chart_x}" y="{y}" width="{chart_w}" height="{bar_h}" rx="8" fill="#EEF3F7"/>')
        parts.append(f'<rect x="{chart_x}" y="{y}" width="{bar_w:.2f}" height="{bar_h}" rx="8" fill="{fill}"/>')
        if bar_w > chart_w - 180:
            parts.append(text(chart_x + bar_w - 20, y + 37, value_label, 24, 700, "#FFFFFF", anchor="end"))
        else:
            parts.append(text(chart_x + bar_w + 20, y + 37, value_label, 24, 700, "#1F2933"))
        parts.append(text(width - margin_x, y + 37, f"n={row['sample_count']}", 20, 500, "#52616B", anchor="end"))

    note_y = height - 120
    note = summary.get("note") or "Profiles are loaded once from PostgreSQL; matching uses the in-memory vector index."
    parts.append(f'<rect x="{margin_x}" y="{note_y - 42}" width="{width - (margin_x * 2)}" height="92" rx="10" fill="#F8FAFC" stroke="#D7DEE6"/>')
    parts.append(text(margin_x + 24, note_y - 8, "Benchmark note", 22, 700, "#1F2933"))
    parts.append(text(margin_x + 24, note_y + 28, note, 21, 400, "#52616B"))
    parts.append(text(margin_x, height - 34, "Source: Static-image-benchmark/benchmark_results.json | Warm-up excluded from averages", 19, 400, "#6B7785"))
    parts.append("</svg>")

    output_path.write_text("\n".join(parts), encoding="utf-8")


def render_png(summary: dict, output_path: Path) -> None:
    rows = stage_rows(summary)
    if not rows:
        raise ValueError("No average benchmark rows found.")

    width, height = 1600, 1000
    image = np.full((height, width, 3), 255, dtype=np.uint8)

    def put(text, x, y, scale=0.8, color=(31, 41, 51), thickness=2):
        cv2.putText(image, str(text), (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

    def rect(x1, y1, x2, y2, color, thickness=-1):
        cv2.rectangle(image, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)

    rect(0, 0, width, 18, (187, 114, 0))
    put("Static Image Recognition Benchmark", 92, 92, 1.35, thickness=3)
    put("Average processing time per recognition stage, measured from saved test images", 92, 142, 0.72, (82, 97, 107), 2)

    cards = [
        ("Images processed", summary.get("processed_images", 0)),
        ("Images skipped", summary.get("skipped_images", 0)),
        ("Users loaded", summary.get("users_loaded", 0)),
        ("Embeddings loaded", summary.get("embeddings_loaded", 0)),
    ]
    for idx, (label, value) in enumerate(cards):
        x = 92 + idx * 358
        rect(x, 190, x + 330, 282, (250, 247, 244))
        cv2.rectangle(image, (x, 190), (x + 330, 282), (230, 222, 215), 2)
        put(label, x + 24, 230, 0.62, (82, 97, 107), 2)
        display = f"{value:,}" if isinstance(value, int) else str(value)
        put(display, x + 24, 268, 0.95, (31, 41, 51), 2)

    chart_x, chart_y, chart_w = 520, 330, 840
    bar_h, row_gap = 56, 42
    max_value = max(row["average_ms"] for row in rows)
    colors = {
        "face_detection_yolov8_ms": (187, 114, 0),
        "arcface_embedding_ms": (50, 125, 46),
        "facenet_embedding_ms": (0, 79, 138),
        "database_query_comparison_ms": (117, 108, 99),
        "total_end_to_end_ms": (47, 27, 203),
    }

    put("Processing Stage", 92, chart_y - 42, 0.7, thickness=2)
    put("Average Time", chart_x, chart_y - 42, 0.7, thickness=2)
    cv2.line(image, (92, chart_y - 18), (width - 92, chart_y - 18), (215, 222, 230), 2)

    for idx, row in enumerate(rows):
        y = chart_y + idx * (bar_h + row_gap)
        bar_w = max(4, int((row["average_ms"] / max_value) * chart_w))
        value_label = _fmt_ms(row["average_ms"])
        put(row["label"], 92, y + 38, 0.64, thickness=2)
        rect(chart_x, y, chart_x + chart_w, y + bar_h, (247, 243, 238))
        rect(chart_x, y, chart_x + bar_w, y + bar_h, colors.get(row["key"], (187, 114, 0)))
        if bar_w > chart_w - 180:
            text_size = cv2.getTextSize(value_label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0]
            put(value_label, chart_x + bar_w - text_size[0] - 20, y + 38, 0.65, (255, 255, 255), 2)
        else:
            put(value_label, chart_x + bar_w + 20, y + 38, 0.65, thickness=2)
        put(f"n={row['sample_count']}", width - 190, y + 38, 0.55, (82, 97, 107), 2)

    note = summary.get("note") or "Profiles are loaded once from PostgreSQL; matching uses the in-memory vector index."
    rect(92, height - 162, width - 92, height - 70, (252, 250, 248))
    cv2.rectangle(image, (92, height - 162), (width - 92, height - 70), (215, 222, 230), 2)
    put("Benchmark note", 116, height - 126, 0.62, thickness=2)
    put(note, 116, height - 90, 0.55, (82, 97, 107), 2)
    put("Source: Static-image-benchmark/benchmark_results.json | Warm-up excluded from averages", 92, height - 34, 0.5, (107, 119, 133), 1)

    cv2.imwrite(str(output_path), image)


def render_visuals(results_path: Path, output_dir: Path) -> tuple[Path, Path]:
    summary = load_summary(results_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / "benchmark_summary_figure.svg"
    png_path = output_dir / "benchmark_summary_figure.png"
    render_svg(summary, svg_path)
    render_png(summary, png_path)
    return svg_path, png_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render thesis-ready benchmark visuals from benchmark_results.json.")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH), help="Path to benchmark_results.json.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for SVG and PNG outputs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        svg_path, png_path = render_visuals(Path(args.results), Path(args.output_dir))
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"Saved SVG: {svg_path}")
    print(f"Saved PNG: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
