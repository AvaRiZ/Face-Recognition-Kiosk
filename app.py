from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from ultralytics import YOLO

from app.cli import CLIApplication
from app.flask_app import create_flask_app
from auth import init_auth_db
from core.config import AppConfig, configure_devices, resolve_yolo_device
from core.state import AppStateManager
from database.repository import UserRepository
from routes.routes import init_imported_logs_table
from services.embedding_service import EmbeddingService
from services.quality_service import FaceQualityService
from services.recognition_service import FaceRecognitionService
from services.dataset_service import DetectorDatasetService
from services.staff_service import ensure_profile_upload_dir
from services.tracking_service import TrackingService
from utils.logging import log_gpu_info, log_header, log_step


@dataclass
class AppRuntime:
    config: AppConfig
    state: AppStateManager
    repository: UserRepository
    cli: CLIApplication


def build_runtime() -> AppRuntime:
    config = AppConfig()

    configure_devices(
        torch_device_index=config.torch_device_index,
        tf_use_gpu=config.tf_use_gpu,
        logger=log_step,
    )
    log_gpu_info()

    os.makedirs(config.base_save_dir, exist_ok=True)
    ensure_profile_upload_dir()
    DetectorDatasetService(config).ensure_structure()

    repository = UserRepository(config.db_path)
    repository.init_db()
    init_auth_db()
    init_imported_logs_table(config.db_path)

    state = AppStateManager(config)
    state.load_users(repository.get_all_users())

    log_step("Loading YOLOv8 face detection model...")
    yolo_device = resolve_yolo_device(config.torch_device_index)
    yolo_model = YOLO(config.model_path)
    try:
        yolo_model.to(yolo_device)
    except Exception as exc:
        log_step(f"YOLO device warning: {exc}", status="WARN")
    log_step(f"YOLOv8 model loaded on {yolo_device}")

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

    return AppRuntime(config=config, state=state, repository=repository, cli=cli)


def start_web_server(host: str, port: int, debug: bool, runtime: AppRuntime):
    app = create_flask_app(runtime.config, runtime.state, runtime.repository, runtime.cli)
    server_thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": host,
            "port": port,
            "debug": debug,
            "use_reloader": False,
        },
        daemon=True,
        name="flask-web-server",
    )
    server_thread.start()
    return server_thread


def main() -> None:
    runtime = build_runtime()
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    stream_url = os.environ.get("CCTV_STREAM_URL", "0").strip() or "0"

    log_header("CCTV Face Recognition System - Initialization")
    log_step(f"Database: {runtime.config.db_path}")
    log_step(f"Face models: {runtime.config.primary_model} + {runtime.config.secondary_model}")
    log_step(f"Base threshold: {runtime.state.base_threshold}")
    log_step(f"Users in database: {runtime.state.user_count}")
    log_step(f"The website is running at the same time with detection and recognition.")
    log_step(f"The register is only on the website, there should be no options on the terminal.")
    log_step(f"Starting web server at http://{host}:{port}")
    log_step(f"Starting detection and recognition using stream source: {stream_url}")

    start_web_server(host, port, debug, runtime)
    runtime.cli.process_cctv_stream(stream_url)


if __name__ == "__main__":
    main()
