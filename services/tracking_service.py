from __future__ import annotations

import time

from core.config import AppConfig
from core.state import AppStateManager


class TrackingService:
    def __init__(self, config: AppConfig, state: AppStateManager):
        self.config = config
        self.state = state

    def initialize_track_state(self, track_id: int, current_time: float):
        return self.state.initialize_track_state(track_id, current_time)

    def cleanup_stale_tracks(self, current_time: float) -> list[int]:
        stale_cutoff = current_time - self.config.track_stale_seconds
        stale_track_ids = [
            track_id
            for track_id, track_state in self.state.tracked_faces.items()
            if track_state.last_seen < stale_cutoff
        ]
        for track_id in stale_track_ids:
            self.state.remove_track_state(track_id)
        return stale_track_ids

    def check_face_stability(self, face_id: int, x1: int, y1: int, x2: int, y2: int) -> bool:
        current_time = time.time()
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        tracker = self.state.get_or_create_face_stability(face_id)

        if not tracker.positions:
            tracker.positions = [(center_x, center_y)]
            tracker.timestamps = [current_time]
            tracker.stable_since = None
            return False

        last_x, last_y = tracker.positions[-1]
        distance = ((center_x - last_x) ** 2 + (center_y - last_y) ** 2) ** 0.5

        if distance <= self.config.position_tolerance:
            tracker.positions.append((center_x, center_y))
            tracker.timestamps.append(current_time)

            cutoff_time = current_time - 5.0
            valid_indices = [i for i, ts in enumerate(tracker.timestamps) if ts >= cutoff_time]
            tracker.positions = [tracker.positions[i] for i in valid_indices]
            tracker.timestamps = [tracker.timestamps[i] for i in valid_indices]

            if len(tracker.timestamps) >= 2:
                stable_duration = tracker.timestamps[-1] - tracker.timestamps[0]
                if stable_duration >= self.config.stability_time_required:
                    if tracker.stable_since is None:
                        tracker.stable_since = current_time
                    return True
        else:
            tracker.positions = [(center_x, center_y)]
            tracker.timestamps = [current_time]
            tracker.stable_since = None

        return False
