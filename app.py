from __future__ import annotations

import argparse
import os
import threading
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


def parse_args():
    parser = argparse.ArgumentParser(description="Face Recognition Kiosk")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run the Flask web interface alongside the interactive CLI menu.",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Run only the Flask web interface.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        help="Host interface for web mode.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASK_RUN_PORT", 5000)),
        help="Port for web mode.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode in web mode.",
    )
    return parser.parse_args()


def start_web_server(host: str, port: int, debug: bool, runtime: AppRuntime):
    app = create_flask_app(runtime.config, runtime.state, runtime.repository)
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
    args = parse_args()
    runtime = build_runtime()

    log_header("CCTV Face Recognition System - Initialization")
    log_step(f"Database: {runtime.config.db_path}")
    log_step(f"Face models: {runtime.config.primary_model} + {runtime.config.secondary_model}")
    log_step(f"Base threshold: {runtime.state.base_threshold}")
    log_step(f"Users in database: {runtime.state.user_count}")

    if args.web_only:
        log_step(f"Starting web server at http://{args.host}:{args.port}")
        create_flask_app(runtime.config, runtime.state, runtime.repository).run(
            host=args.host,
            port=args.port,
            debug=args.debug,
        )
        return

    if args.web:
        log_step(f"Starting web server at http://{args.host}:{args.port}")
        start_web_server(args.host, args.port, args.debug, runtime)

    runtime.cli.main_menu()


if __name__ == "__main__":
    main()
