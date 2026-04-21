from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

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
    )
    return HostRuntime(config=config, state=state, repository=repository, cli=cli)


def main() -> None:
    db_target = resolve_database_target(AppConfig().db_path)
    if not is_postgres_target(db_target):
        raise RuntimeError(
            "This architecture requires PostgreSQL as the persistent datastore. "
            "Set DATABASE_URL to a postgres://, postgresql://, or postgresql+<driver>:// target."
        )

    runtime = build_runtime()
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    stream_source = runtime.config.resolved_cctv_stream_source()

    app = create_flask_app(runtime.config, runtime.state, runtime.repository, cli=runtime.cli)

    log_header("Library Entrance Face Recognition Host Stack")
    log_step(f"Database target: {db_target}")
    log_step(f"Users in database: {runtime.state.user_count}")
    log_step(f"Serving dashboard and API at http://{host}:{port}")
    log_step(f"Starting detection and recognition using stream source: {stream_source}")

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

    runtime.cli.process_cctv_stream(stream_source)


if __name__ == "__main__":
    main()
