"""Background task scheduler for occupancy snapshots and maintenance.

Responsibilities:
- Generate occupancy snapshots at regular intervals (e.g., every 5 minutes)
- Log occupancy state to database for historical analysis
- Support graceful shutdown
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

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
        from services.occupancy_service import OccupancyService

        config = AppConfig()
        service = OccupancyService(config.db_path)

        next_run = datetime.now(timezone.utc)

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)

            if now >= next_run:
                try:
                    service.create_snapshot(config.max_library_capacity)
                    next_run = now.replace(microsecond=0) + __import__("datetime").timedelta(
                        seconds=self.interval_seconds
                    )
                except Exception as exc:
                    log_step(
                        f"Occupancy snapshot generation failed: {exc}",
                        status="WARN",
                    )
                    # Schedule next attempt soon
                    next_run = now.replace(microsecond=0) + __import__("datetime").timedelta(seconds=10)

            # Check for stop signal every 1 second
            self._stop_event.wait(timeout=1.0)
