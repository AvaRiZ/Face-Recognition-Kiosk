from __future__ import annotations

import argparse
from pathlib import Path

import cv2


OUTPUT_DIR = Path("real_val_dataset") / "images" / "val"
DEFAULT_STREAM = "0"
DEFAULT_MAX_FRAMES = 100
DEFAULT_EVERY_N_FRAMES = 30
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720


def parse_args():
    parser = argparse.ArgumentParser(description="Extract sample validation frames from a CCTV stream or video file.")
    parser.add_argument(
        "--source",
        default=DEFAULT_STREAM,
        help="Camera index, stream URL, or video file path. Default is 0.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=DEFAULT_MAX_FRAMES,
        help="Maximum number of frames to save.",
    )
    parser.add_argument(
        "--every-n-frames",
        type=int,
        default=DEFAULT_EVERY_N_FRAMES,
        help="Save one frame every N frames read.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help="Target frame width.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help="Target frame height.",
    )
    return parser.parse_args()


def open_capture(source: str):
    if source.isdigit():
        camera_index = int(source)
        if hasattr(cv2, "CAP_DSHOW"):
            return cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        return cv2.VideoCapture(camera_index)

    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if capture.isOpened():
        return capture
    return cv2.VideoCapture(source)


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    capture = open_capture(str(args.source).strip())
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open source: {args.source}")

    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    frame_index = 0
    saved_count = 0

    try:
        while saved_count < args.max_frames:
            ok, frame = capture.read()
            if not ok:
                break

            frame_index += 1
            if frame_index % max(args.every_n_frames, 1) != 0:
                continue

            output_path = OUTPUT_DIR / f"frame_{saved_count + 1:04d}.jpg"
            if cv2.imwrite(str(output_path), frame):
                saved_count += 1
                print(f"Saved {output_path}")
    finally:
        capture.release()

    print()
    print(f"Frames saved: {saved_count}")
    print(f"Output directory: {OUTPUT_DIR.resolve()}")
    print("Next step: create matching YOLO label files in real_val_dataset/labels/val.")


if __name__ == "__main__":
    main()
