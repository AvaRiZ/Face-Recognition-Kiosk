from __future__ import annotations

import os

os.environ.setdefault("WORKER_ROLE", "exit")
os.environ.setdefault("WORKER_STATION_ID", "exit-station-1")
os.environ.setdefault("WORKER_CAMERA_ID", "2")
os.environ.setdefault("WORKER_CCTV_STREAM_SOURCE", "1")

from workers.recognition_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
