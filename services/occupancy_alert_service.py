from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OccupancyAlertState:
    level: str
    should_block: bool


class OccupancyAlertService:
    """Compute occupancy alert levels and detect status changes."""

    def __init__(self) -> None:
        self._last_state: OccupancyAlertState | None = None

    @staticmethod
    def _level_from_ratio(
        occupancy_ratio: float,
        is_full: bool,
        warning_threshold: float,
        moderate_threshold: float,
    ) -> str:
        if is_full:
            return "full"
        if occupancy_ratio >= warning_threshold:
            return "warning"
        if occupancy_ratio >= moderate_threshold:
            return "moderate"
        return "ok"

    def evaluate(
        self,
        *,
        occupancy_count: int,
        capacity_limit: int,
        occupancy_ratio: float,
        is_full: bool,
        capacity_warning: bool,
        warning_threshold: float,
        moderate_threshold: float,
        state_is_stale: bool,
    ) -> tuple[dict, bool]:
        if state_is_stale:
            level = "conservative"
            should_block = False
        else:
            level = self._level_from_ratio(
                occupancy_ratio,
                is_full,
                warning_threshold,
                moderate_threshold,
            )
            should_block = False

        payload = {
            "level": level,
            "should_block": should_block,
            "occupancy_count": int(occupancy_count),
            "capacity_limit": int(capacity_limit),
            "occupancy_ratio": float(occupancy_ratio),
            "capacity_warning": bool(capacity_warning),
            "is_full": bool(is_full),
            "state_is_stale": bool(state_is_stale),
        }

        changed = False
        if self._last_state is None:
            changed = True
        else:
            changed = (
                self._last_state.level != level
                or self._last_state.should_block != should_block
            )

        self._last_state = OccupancyAlertState(level=level, should_block=should_block)
        return payload, changed


occupancy_alert_service = OccupancyAlertService()
