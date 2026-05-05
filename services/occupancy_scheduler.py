"""Background task scheduler for occupancy snapshots and maintenance.

Responsibilities:
- Generate occupancy snapshots at regular intervals (e.g., every 5 minutes)
- Log occupancy state to database for historical analysis
- Support graceful shutdown
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from db import connect as db_connect
from db import get_app_setting
from utils.logging import log_step


class OccupancySnapshotScheduler:
    """Scheduler for periodic occupancy snapshot generation."""

    def __init__(self, db_path: str, interval_seconds: int = 300, auto_start: bool = True) -> None:
        """
        Initialize the scheduler.

        Args:
            db_path: Path to the database
            interval_seconds: How often to generate snapshots (default: 300 = 5 min)
            auto_start: Whether to start the scheduler immediately
        """
        self.db_path = db_path
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = None

        if auto_start:
            self.start()

    def start(self) -> None:
        """Start the snapshot scheduler in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            log_step(
                "Occupancy snapshot scheduler is already running.",
                status="WARN",
            )
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="occupancy-snapshot-scheduler",
        )
        self._thread.start()
        log_step(
            f"Occupancy snapshot scheduler started (interval: {self.interval_seconds}s)",
            status="OK",
        )

    def stop(self) -> None:
        """Stop the snapshot scheduler gracefully."""
        if self._thread is None:
            return

        log_step("Stopping occupancy snapshot scheduler...", status="INFO")
        self._stop_event.set()

        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

        log_step("Occupancy snapshot scheduler stopped.", status="OK")

    def _run_loop(self) -> None:
        """Main loop for generating snapshots at regular intervals."""
        from core.config import AppConfig
        from services.occupancy_service import OccupancyService, resolve_capacity_limit

        config = AppConfig()
        service = OccupancyService(config.db_path)

        next_run = datetime.now(timezone.utc)
        last_seen_date = next_run.date()
        retention_interval = timedelta(hours=6)
        next_retention_run = next_run

        def _resolve_int_setting(key: str, default: int, minimum: int, maximum: int) -> int:
            raw_value = get_app_setting(self.db_path, key, str(default))
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                parsed = int(default)
            return max(int(minimum), min(int(maximum), int(parsed)))

        def _resolve_float_setting(key: str, default: float, minimum: float, maximum: float) -> float:
            raw_value = get_app_setting(self.db_path, key, str(default))
            try:
                parsed = float(raw_value)
            except (TypeError, ValueError):
                parsed = float(default)
            return max(float(minimum), min(float(maximum), float(parsed)))

        def _purge_face_snapshots(base_dir: Path, cutoff_time: datetime) -> set[str]:
            if not base_dir.exists():
                return set()
            removed: set[str] = set()
            cutoff_ts = cutoff_time.timestamp()
            for root, _dirs, files in os.walk(base_dir):
                for filename in files:
                    file_path = Path(root) / filename
                    try:
                        if file_path.stat().st_mtime >= cutoff_ts:
                            continue
                    except Exception:
                        continue
                    try:
                        file_path.unlink()
                        removed.add(str(file_path))
                    except Exception:
                        continue
            return removed

        def _cleanup_user_image_paths(removed_paths: set[str]) -> int:
            if not removed_paths:
                return 0
            conn = db_connect(self.db_path)
            c = conn.cursor()
            c.execute(
                """
                SELECT user_id, image_paths
                FROM users
                WHERE image_paths IS NOT NULL AND TRIM(image_paths) != ''
                """
            )
            rows = c.fetchall()
            updated_rows = 0
            for user_id, raw_paths in rows:
                paths = [path for path in str(raw_paths or "").split(";") if path]
                kept_paths: list[str] = []
                for path_text in paths:
                    if path_text in removed_paths:
                        continue
                    if not Path(path_text).exists():
                        continue
                    kept_paths.append(path_text)
                if kept_paths != paths:
                    c.execute(
                        """
                        UPDATE users
                        SET image_paths = %s, last_updated = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                        """,
                        (";".join(kept_paths), int(user_id)),
                    )
                    updated_rows += 1
            conn.commit()
            conn.close()
            return updated_rows

        def _purge_recognition_events(cutoff_time: datetime) -> int:
            conn = db_connect(self.db_path)
            c = conn.cursor()
            c.execute(
                """
                DELETE FROM recognition_events
                WHERE COALESCE(captured_at, ingested_at) < %s
                """,
                (cutoff_time,),
            )
            deleted_rows = int(c.rowcount or 0)
            conn.commit()
            conn.close()
            return deleted_rows

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            if now.date() != last_seen_date:
                try:
                    # Reconcile yesterday once, shortly after UTC date rollover.
                    reconciliation_date = now.date() - timedelta(days=1)
                    result = service.reconcile_day(reconciliation_date, drift_threshold=5)
                    if result.get("alerted"):
                        log_step(
                            f"Nightly occupancy reconciliation detected drift on {result['date']} "
                            f"(net_drift={result['net_drift']}, threshold={result['threshold']}).",
                            status="WARN",
                        )
                    else:
                        log_step(
                            f"Nightly occupancy reconciliation passed for {result['date']} "
                            f"(net_drift={result['net_drift']}).",
                            status="OK",
                        )
                except Exception as exc:
                    log_step(
                        f"Nightly occupancy reconciliation failed: {exc}",
                        status="WARN",
                    )
                finally:
                    last_seen_date = now.date()

            if now >= next_run:
                try:
                    interval_seconds = _resolve_int_setting(
                        "occupancy_snapshot_interval_seconds",
                        int(config.occupancy_snapshot_interval_seconds),
                        minimum=60,
                        maximum=3600,
                    )
                    if interval_seconds != self.interval_seconds:
                        self.interval_seconds = interval_seconds
                        log_step(
                            f"Occupancy snapshot interval updated to {self.interval_seconds}s",
                            status="INFO",
                        )
                    warning_threshold = _resolve_float_setting(
                        "occupancy_warning_threshold",
                        float(config.occupancy_warning_threshold),
                        minimum=0.5,
                        maximum=0.99,
                    )
                    capacity_limit = resolve_capacity_limit(
                        config.db_path,
                        default=int(config.max_library_capacity),
                    )
                    service.create_snapshot(
                        capacity_limit,
                        warning_threshold=warning_threshold,
                    )
                    next_run = now.replace(microsecond=0) + timedelta(seconds=self.interval_seconds)
                except Exception as exc:
                    log_step(
                        f"Occupancy snapshot generation failed: {exc}",
                        status="WARN",
                    )
                    # Schedule next attempt soon
                    next_run = now.replace(microsecond=0) + timedelta(seconds=10)

            if now >= next_retention_run:
                try:
                    face_retention_days = _resolve_int_setting(
                        "face_snapshot_retention_days",
                        int(getattr(config, "face_snapshot_retention_days", 30)),
                        minimum=1,
                        maximum=365,
                    )
                    event_retention_days = _resolve_int_setting(
                        "recognition_event_retention_days",
                        int(getattr(config, "recognition_event_retention_days", 365)),
                        minimum=1,
                        maximum=3650,
                    )
                    face_cutoff = now - timedelta(days=face_retention_days)
                    removed_paths = _purge_face_snapshots(Path(config.base_save_dir), face_cutoff)
                    cleaned_users = _cleanup_user_image_paths(removed_paths)
                    event_cutoff = now - timedelta(days=event_retention_days)
                    deleted_events = _purge_recognition_events(event_cutoff)
                    if removed_paths or cleaned_users or deleted_events:
                        log_step(
                            f"Retention cleanup completed: removed_files={len(removed_paths)} "
                            f"updated_users={cleaned_users} deleted_events={deleted_events}",
                            status="OK",
                        )
                except Exception as exc:
                    log_step(f"Retention cleanup failed: {exc}", status="WARN")
                finally:
                    next_retention_run = now.replace(microsecond=0) + retention_interval

            # Check for stop signal every 1 second
            self._stop_event.wait(timeout=1.0)
