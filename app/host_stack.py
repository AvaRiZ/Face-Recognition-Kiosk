from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ultralytics import YOLO

from app.cli import CLIApplication
from app.flask_app import create_flask_app
from app.realtime import socketio
from auth import init_auth_db
from core.config import AppConfig, configure_devices, resolve_yolo_device
from core.state import AppStateManager
from database.repository import UserRepository
from database.schema import init_canonical_schema
from db import is_postgres_target, resolve_database_target
from routes.routes import init_imported_logs_table
from services.dataset_service import DetectorDatasetService
from services.embedding_service import EmbeddingService
from services.quality_service import FaceQualityService
from services.recognition_service import FaceRecognitionService
from services.staff_service import ensure_profile_upload_dir
from services.tracking_service import TrackingService
from utils.logging import log_gpu_info, log_header, log_step


@dataclass
class HostRuntime:
    config: AppConfig
    state: AppStateManager
    repository: UserRepository
    cli: CLIApplication


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


def _load_default_local_env(repo_root: Path) -> None:
    _load_env_file_if_present(repo_root / ".env.local")


def build_runtime() -> HostRuntime:
    config = AppConfig()
    os.makedirs(config.base_save_dir, exist_ok=True)
    ensure_profile_upload_dir()
    DetectorDatasetService(config).ensure_structure()

    repository = UserRepository(config.db_path)
    repository.init_db()
    init_auth_db()
    init_imported_logs_table(config.db_path)
    init_canonical_schema(config.db_path)

    state = AppStateManager(config)
    state.load_users(repository.get_all_users())

    configure_devices(
        torch_device_index=config.torch_device_index,
        tf_use_gpu=config.tf_use_gpu,
        logger=log_step,
    )
    log_gpu_info()

    log_step("Loading YOLO face detection model...")
    yolo_device = resolve_yolo_device(config.torch_device_index)
    yolo_model = YOLO(config.model_path)
    try:
        yolo_model.to(yolo_device)
    except Exception as exc:
        log_step(f"YOLO device warning: {exc}", status="WARN")
    log_step(f"YOLO model loaded on {yolo_device}")

    embedding_service = EmbeddingService(config)
    log_step("Warming up embedding models...")
    warmup_started = time.perf_counter()
    embedding_service.warm_up_models(logger=log_step)
    warmup_elapsed = time.perf_counter() - warmup_started
    log_step(f"Embedding warm-up finished in {warmup_elapsed:.2f}s")

    quality_service = FaceQualityService(config)
    tracking_service = TrackingService(config, state)
    recognition_service = FaceRecognitionService(
        config=config,
        state=state,
        repository=repository,
        embedding_service=embedding_service,
    )

    cli = CLIApplication(
        config=config,
        state=state,
        repository=repository,
        quality_service=quality_service,
        recognition_service=recognition_service,
        tracking_service=tracking_service,
        yolo_model=yolo_model,
        yolo_device=yolo_device,
        worker_role="entry",
    )
    return HostRuntime(config=config, state=state, repository=repository, cli=cli)


def _resolve_worker_stream_source(env_name: str, fallback: str) -> str:
    value = (os.environ.get(env_name) or "").strip()
    return value or fallback


def _apply_app_settings(runtime: HostRuntime) -> None:
    from db import get_app_setting

    def _coerce_float(raw_value, fallback, minimum, maximum):
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = float(fallback)
        return max(float(minimum), min(float(maximum), float(parsed)))

    def _coerce_int(raw_value, fallback, minimum, maximum):
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = int(fallback)
        return max(int(minimum), min(int(maximum), int(parsed)))

    config = runtime.config
    state = runtime.state

    threshold = _coerce_float(
        get_app_setting(config.db_path, "threshold", str(state.base_threshold)),
        state.base_threshold,
        0.1,
        0.95,
    )
    quality_threshold = _coerce_float(
        get_app_setting(config.db_path, "quality_threshold", str(state.face_quality_threshold)),
        state.face_quality_threshold,
        0.1,
        0.95,
    )
    state.set_thresholds(threshold, quality_threshold)

    config.recognition_confidence_threshold = _coerce_float(
        get_app_setting(
            config.db_path,
            "recognition_confidence_threshold",
            str(config.recognition_confidence_threshold),
        ),
        config.recognition_confidence_threshold,
        0.1,
        0.99,
    )
    config.vector_index_top_k = _coerce_int(
        get_app_setting(config.db_path, "vector_index_top_k", str(config.vector_index_top_k)),
        config.vector_index_top_k,
        1,
        100,
    )
    config.max_library_capacity = _coerce_int(
        get_app_setting(config.db_path, "max_occupancy", str(config.max_library_capacity)),
        config.max_library_capacity,
        50,
        2000,
    )
    config.occupancy_warning_threshold = _coerce_float(
        get_app_setting(
            config.db_path,
            "occupancy_warning_threshold",
            str(config.occupancy_warning_threshold),
        ),
        config.occupancy_warning_threshold,
        0.5,
        0.99,
    )
    config.occupancy_snapshot_interval_seconds = _coerce_int(
        get_app_setting(
            config.db_path,
            "occupancy_snapshot_interval_seconds",
            str(config.occupancy_snapshot_interval_seconds),
        ),
        config.occupancy_snapshot_interval_seconds,
        60,
        3600,
    )
    config.face_snapshot_retention_days = _coerce_int(
        get_app_setting(
            config.db_path,
            "face_snapshot_retention_days",
            str(getattr(config, "face_snapshot_retention_days", 30)),
        ),
        getattr(config, "face_snapshot_retention_days", 30),
        1,
        365,
    )
    config.recognition_event_retention_days = _coerce_int(
        get_app_setting(
            config.db_path,
            "recognition_event_retention_days",
            str(getattr(config, "recognition_event_retention_days", 365)),
        ),
        getattr(config, "recognition_event_retention_days", 365),
        1,
        3650,
    )

    entry_source = str(
        get_app_setting(config.db_path, "entry_cctv_stream_source", str(config.entry_cctv_stream_source))
        or ""
    ).strip()
    if entry_source:
        config.entry_cctv_stream_source = entry_source

    exit_source = str(
        get_app_setting(config.db_path, "exit_cctv_stream_source", str(config.exit_cctv_stream_source))
        or ""
    ).strip()
    if exit_source:
        config.exit_cctv_stream_source = exit_source


def _start_worker_process(
    repo_root: Path,
    worker_role: str,
    station_id: str,
    camera_id: int,
    stream_source: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["WORKER_ROLE"] = worker_role
    env["WORKER_STATION_ID"] = station_id
    env["WORKER_CAMERA_ID"] = str(camera_id)
    env["WORKER_CCTV_STREAM_SOURCE"] = stream_source
    return subprocess.Popen([sys.executable, "-m", "workers.recognition_worker"], cwd=str(repo_root), env=env)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _load_default_local_env(repo_root)

    db_target = resolve_database_target(AppConfig().db_path)
    if not is_postgres_target(db_target):
        raise RuntimeError(
            "This architecture requires PostgreSQL as the persistent datastore. "
            "Set DATABASE_URL to a postgres://, postgresql://, or postgresql+<driver>:// target."
        )

    runtime = build_runtime()
    _apply_app_settings(runtime)
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    # Use config values for stream sources, but allow environment variable overrides.
    entry_stream_source = _resolve_worker_stream_source("ENTRY_CCTV_STREAM_SOURCE", str(runtime.config.resolved_entry_stream_source()))
    exit_stream_source = _resolve_worker_stream_source("EXIT_CCTV_STREAM_SOURCE", str(runtime.config.resolved_exit_stream_source()))

    app = create_flask_app(runtime.config, runtime.state, runtime.repository, cli=runtime.cli)

    log_header("Library Entrance Face Recognition Host Stack")
    log_step(f"Database target: {db_target}")
    log_step(f"Users in database: {runtime.state.user_count}")
    log_step(f"Serving dashboard and API at http://{host}:{port}")
    log_step(f"Starting dual workers: entry={entry_stream_source}, exit={exit_stream_source}")

    # Start occupancy snapshot scheduler
    from services.occupancy_scheduler import OccupancySnapshotScheduler
    occupancy_scheduler = OccupancySnapshotScheduler(
        db_path=runtime.config.db_path,
        interval_seconds=runtime.config.occupancy_snapshot_interval_seconds,
        auto_start=True,
    )

    api_thread = threading.Thread(
        target=socketio.run,
        kwargs={
            "app": app,
            "host": host,
            "port": port,
            "debug": debug,
            "use_reloader": False,
            "allow_unsafe_werkzeug": True,
        },
        daemon=True,
        name="host-api-server",
    )
    api_thread.start()
    time.sleep(0.5)

    runtime.cli.resume_detection()
    runtime.cli._set_stream_status("live", "Dual camera workers are running in separate processes.")

    worker_processes = {
        "entry": _start_worker_process(repo_root, "entry", "entry-station-1", 1, entry_stream_source),
        "exit": _start_worker_process(repo_root, "exit", "exit-station-1", 2, exit_stream_source),
    }

    try:
        while True:
            for worker_role, proc in list(worker_processes.items()):
                code = proc.poll()
                if code is not None:
                    log_step(
                        f"Worker '{worker_role}' exited with code {code}. Host API will continue running.",
                        status="WARN",
                    )
                    worker_processes.pop(worker_role, None)
            time.sleep(1.0)
    except KeyboardInterrupt:
        log_step("Shutdown requested. Stopping worker processes...", status="WARN")
    finally:
        occupancy_scheduler.stop()
        for proc in worker_processes.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in worker_processes.values():
            try:
                proc.wait(timeout=10)
            except Exception:
                if proc.poll() is None:
                    proc.kill()


if __name__ == "__main__":
    main()
