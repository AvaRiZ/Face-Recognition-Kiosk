from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from ultralytics import YOLO

from app.cli import CLIApplication
from core.config import AppConfig, configure_devices, resolve_yolo_device
from core.state import AppStateManager
from services.embedding_service import EmbeddingService
from services.quality_service import FaceQualityService
from services.recognition_service import FaceRecognitionService
from services.tracking_service import TrackingService
from utils.logging import log_gpu_info, log_header, log_step
from workers.api_client import ApiClient
from workers.durable_queue import DurableOutboundQueue
from workers.worker_repository import WorkerApiRepository


@dataclass
class WorkerRuntime:
    config: AppConfig
    state: AppStateManager
    repository: WorkerApiRepository
    queue: DurableOutboundQueue
    api_client: ApiClient
    cli: CLIApplication
    worker_role: str
    station_id: str
    camera_id: int
    stream_source: str | int


def _resolve_worker_context() -> tuple[str, str, int, str | int]:
    from core.config import AppConfig
    
    config = AppConfig()
    worker_role = (os.environ.get("WORKER_ROLE") or "entry").strip().lower()
    if worker_role not in {"entry", "exit"}:
        worker_role = "entry"

    station_id = (os.environ.get("WORKER_STATION_ID") or "").strip()
    if not station_id:
        station_id = "entry-station-1" if worker_role == "entry" else "exit-station-1"

    default_camera_id = 1 if worker_role == "entry" else 2
    camera_id_raw = (os.environ.get("WORKER_CAMERA_ID") or "").strip()
    try:
        camera_id = int(camera_id_raw) if camera_id_raw else default_camera_id
    except (TypeError, ValueError):
        camera_id = default_camera_id

    # Use config-based defaults for stream source, with environment override
    if worker_role == "entry":
        config_stream_default = str(config.resolved_entry_stream_source())
    else:
        config_stream_default = str(config.resolved_exit_stream_source())
    
    stream_source = (os.environ.get("WORKER_CCTV_STREAM_SOURCE") or "").strip()
    if not stream_source:
        stream_source = config_stream_default
    if stream_source.isdigit():
        return worker_role, station_id, camera_id, int(stream_source)
    return worker_role, station_id, camera_id, stream_source


def _send_outbound_entry(api_client: ApiClient, entry: dict) -> bool:
    kind = str(entry.get("kind") or "")
    payload = entry.get("payload") or {}

    if kind == "recognition_event":
        response = api_client.post_json("/api/internal/recognition-events", payload)
        return bool(response.get("success"))
    if kind == "embedding_update":
        response = api_client.post_json("/api/internal/embedding-updates", payload)
        return bool(response.get("success"))
    return True


def _safe_fetch_profiles(repository: WorkerApiRepository) -> list:
    try:
        return repository.get_all_users()
    except Exception as exc:
        log_step(f"Profile snapshot fetch failed: {exc}", status="WARN")
        return []


def _apply_runtime_config(runtime: WorkerRuntime, payload: dict) -> None:
    base_threshold = float(payload.get("base_threshold", runtime.state.base_threshold))
    quality_threshold = float(payload.get("face_quality_threshold", runtime.state.face_quality_threshold))
    runtime.state.set_thresholds(base_threshold, quality_threshold)
    vector_index_top_k = int(payload.get("vector_index_top_k", runtime.config.vector_index_top_k))
    runtime.config.vector_index_top_k = max(1, vector_index_top_k)
    recognition_confidence_threshold = float(
        payload.get("recognition_confidence_threshold", runtime.config.recognition_confidence_threshold)
    )
    runtime.config.recognition_confidence_threshold = max(0.0, min(1.0, recognition_confidence_threshold))


def _start_sync_loop(runtime: WorkerRuntime, poll_interval_seconds: float = 3.0):
    status = {"profiles_version": 0, "settings_version": 0}

    def _loop():
        while True:
            try:
                sent, remaining = runtime.queue.drain_once(
                    lambda entry: _send_outbound_entry(runtime.api_client, entry)
                )
                if sent > 0:
                    log_step(f"Flushed worker queue: sent={sent}, remaining={remaining}")

                profiles_version_payload = runtime.api_client.get_json("/api/internal/profiles/version")
                api_profiles_version = int(profiles_version_payload.get("profiles_version") or 0)
                if api_profiles_version != status["profiles_version"]:
                    users = _safe_fetch_profiles(runtime.repository)
                    runtime.state.load_users(users)
                    status["profiles_version"] = api_profiles_version
                    log_step(f"Loaded profile snapshot version={api_profiles_version} users={len(users)}")

                runtime_config_payload = runtime.api_client.get_json("/api/internal/runtime-config")
                api_settings_version = int(runtime_config_payload.get("settings_version") or 0)
                if api_settings_version != status["settings_version"]:
                    _apply_runtime_config(runtime, runtime_config_payload)
                    status["settings_version"] = api_settings_version
                    log_step(f"Applied runtime settings version={api_settings_version}")
            except Exception as exc:
                log_step(f"Worker sync warning: {exc}", status="WARN")
            time.sleep(max(1.0, float(poll_interval_seconds)))

    thread = threading.Thread(target=_loop, daemon=True, name="worker-sync-loop")
    thread.start()
    return thread


def build_runtime() -> WorkerRuntime:
    config = AppConfig()
    worker_role, station_id, camera_id, stream_source = _resolve_worker_context()
    configure_devices(
        torch_device_index=config.torch_device_index,
        tf_use_gpu=config.tf_use_gpu,
        logger=log_step,
    )
    log_gpu_info()

    api_base_url = (os.environ.get("WORKER_API_BASE_URL") or "http://127.0.0.1:5000").strip()
    token = (os.environ.get("WORKER_INTERNAL_TOKEN") or "").strip()
    queue_dir = (os.environ.get("WORKER_QUEUE_DIR") or "data/worker_queue").strip()

    api_client = ApiClient(base_url=api_base_url, token=token)
    queue = DurableOutboundQueue(queue_dir=queue_dir)
    repository = WorkerApiRepository(
        api_client=api_client,
        outbound_queue=queue,
        station_id=station_id,
        camera_id=camera_id,
    )
    state = AppStateManager(config)

    users = _safe_fetch_profiles(repository)
    state.load_users(users)

    try:
        runtime_payload = api_client.get_json("/api/internal/runtime-config")
        base_threshold = float(runtime_payload.get("base_threshold", state.base_threshold))
        quality_threshold = float(runtime_payload.get("face_quality_threshold", state.face_quality_threshold))
        state.set_thresholds(base_threshold, quality_threshold)
        config.vector_index_top_k = max(
            1,
            int(runtime_payload.get("vector_index_top_k", config.vector_index_top_k)),
        )
        config.recognition_confidence_threshold = max(
            0.0,
            min(
                1.0,
                float(
                    runtime_payload.get(
                        "recognition_confidence_threshold",
                        config.recognition_confidence_threshold,
                    )
                ),
            ),
        )
    except Exception as exc:
        log_step(f"Runtime config fetch failed; using local defaults ({exc})", status="WARN")

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
    return WorkerRuntime(
        config=config,
        state=state,
        repository=repository,
        queue=queue,
        api_client=api_client,
        cli=cli,
        worker_role=worker_role,
        station_id=station_id,
        camera_id=camera_id,
        stream_source=stream_source,
    )


def main() -> None:
    runtime = build_runtime()
    api_url = (os.environ.get("WORKER_API_BASE_URL") or "http://127.0.0.1:5000").strip()

    log_header(f"Library {runtime.worker_role.title()} Recognition Worker")
    log_step(f"API target: {api_url}")
    log_step(f"Worker route: role={runtime.worker_role} station_id={runtime.station_id} camera_id={runtime.camera_id}")
    log_step(f"Users in worker cache: {runtime.state.user_count}")
    log_step(f"Starting detection and recognition using stream source: {runtime.stream_source}")

    _start_sync_loop(runtime)
    runtime.cli.process_cctv_stream(runtime.stream_source, window_title=f"{runtime.worker_role.title()} Camera Recognition")


if __name__ == "__main__":
    main()
