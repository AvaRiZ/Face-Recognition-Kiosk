"""Occupancy Service - Manages library occupancy tracking and capacity monitoring.

Responsibilities:
- Calculate current occupancy (entries - exits for today)
- Generate occupancy snapshots at regular intervals
- Check occupancy against capacity limits
- Detect capacity breaches and anomalies
- Provide historical occupancy data for analytics
"""
from __future__ import annotations

from datetime import datetime, date, timezone

from db import connect as db_connect


class OccupancyService:
    """Manages occupancy calculations and snapshots."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def record_event(self, camera_id: int, captured_at: datetime | None = None) -> dict:
        """Record a single recognition event into the daily occupancy state."""
        event_time = captured_at or datetime.now(timezone.utc)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        event_time = event_time.astimezone(timezone.utc)
        state_date = event_time.date().isoformat()

        daily_entries = 1 if int(camera_id) == 1 else 0
        daily_exits = 1 if int(camera_id) == 2 else 0

        conn = db_connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(state_date) DO UPDATE SET
                daily_entries = daily_occupancy_state.daily_entries + excluded.daily_entries,
                daily_exits = daily_occupancy_state.daily_exits + excluded.daily_exits,
                updated_at = excluded.updated_at
            """,
            (state_date, daily_entries, daily_exits, event_time),
        )

        conn.commit()

        c.execute(
            """
            SELECT state_date, daily_entries, daily_exits, updated_at
            FROM daily_occupancy_state
            WHERE state_date = ?
            """,
            (state_date,),
        )
        row = c.fetchone()
        conn.close()

        if not row:
            return {
                "state_date": state_date,
                "daily_entries": 0,
                "daily_exits": 0,
                "occupancy_count": 0,
                "updated_at": event_time.isoformat(),
            }

        daily_entries = int(row[1] or 0)
        daily_exits = int(row[2] or 0)
        occupancy_count = max(0, daily_entries - daily_exits)
        return {
            "state_date": row[0],
            "daily_entries": daily_entries,
            "daily_exits": daily_exits,
            "occupancy_count": occupancy_count,
            "updated_at": row[3],
        }

    def get_daily_state(self, target_date: date | None = None) -> dict | None:
        """Return the tracked daily occupancy state for a date, if present."""
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT state_date, daily_entries, daily_exits, updated_at
            FROM daily_occupancy_state
            WHERE state_date = ?
            """,
            (target_date.isoformat(),),
        )
        row = c.fetchone()
        conn.close()
        if not row:
            return None

        daily_entries = int(row[1] or 0)
        daily_exits = int(row[2] or 0)
        occupancy_count = max(0, daily_entries - daily_exits)
        return {
            "state_date": row[0],
            "daily_entries": daily_entries,
            "daily_exits": daily_exits,
            "occupancy_count": occupancy_count,
            "updated_at": row[3],
        }

    def calculate_occupancy(self, target_date: date | None = None) -> dict:
        """
        Calculate occupancy for a given date (default: today in UTC).

        Returns dict with:
        - daily_entries (int): number of entry events today
        - daily_exits (int): number of exit events today
        - occupancy_count (int): entries - exits
        - date_str (str): ISO format date string
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        conn = db_connect(self.db_path)
        c = conn.cursor()

        date_str = target_date.isoformat()

        # Count entry events (entered_at) for the target date
        c.execute(
            """
            SELECT COUNT(*) FROM recognition_events
            WHERE entered_at IS NOT NULL AND DATE(entered_at) = ?
            """,
            (date_str,),
        )
        daily_entries = c.fetchone()[0] or 0

        # Count exit events (exited_at) for the target date
        c.execute(
            """
            SELECT COUNT(*) FROM recognition_events
            WHERE exited_at IS NOT NULL AND DATE(exited_at) = ?
            """,
            (date_str,),
        )
        daily_exits = c.fetchone()[0] or 0

        conn.close()

        occupancy_count = daily_entries - daily_exits

        return {
            "daily_entries": daily_entries,
            "daily_exits": daily_exits,
            "occupancy_count": occupancy_count,
            "date_str": date_str,
        }

    def get_current_occupancy(self, capacity_limit: int) -> dict:
        """
        Get current occupancy with capacity status.

        Returns dict with:
        - occupancy_count (int)
        - capacity_limit (int)
        - occupancy_ratio (float: 0.0-1.0)
        - is_full (bool): occupancy >= capacity
        - capacity_warning (bool): occupancy >= 0.9 * capacity
        """
        occ = self.get_daily_state() or self.calculate_occupancy()

        occupancy_count = max(0, occ["occupancy_count"])  # Never negative
        occupancy_ratio = (
            occupancy_count / capacity_limit if capacity_limit > 0 else 0.0
        )
        is_full = occupancy_count >= capacity_limit
        capacity_warning = occupancy_ratio >= 0.90

        return {
            "occupancy_count": occupancy_count,
            "capacity_limit": capacity_limit,
            "occupancy_ratio": round(occupancy_ratio, 3),
            "is_full": is_full,
            "capacity_warning": capacity_warning,
            "daily_entries": occ["daily_entries"],
            "daily_exits": occ["daily_exits"],
            "updated_at": occ.get("updated_at"),
        }

    def create_snapshot(self, capacity_limit: int) -> None:
        """
        Create a point-in-time snapshot of current occupancy and save to database.

        This should be called periodically (e.g., every 5 minutes) and whenever
        capacity status changes.
        """
        occ_data = self.get_current_occupancy(capacity_limit)

        conn = db_connect(self.db_path)
        c = conn.cursor()

        now_utc = datetime.now(timezone.utc)

        c.execute(
            """
            INSERT INTO occupancy_snapshots
            (snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
             daily_entries, daily_exits, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc,
                occ_data["occupancy_count"],
                occ_data["capacity_limit"],
                occ_data["capacity_warning"],
                occ_data["daily_entries"],
                occ_data["daily_exits"],
                now_utc,
            ),
        )

        conn.commit()
        conn.close()

    def get_history(
        self, target_date: date | None = None, limit: int = 288
    ) -> list[dict]:
        """
        Get occupancy snapshots for a given date (default: today in UTC).

        Args:
            target_date: Date to query (ISO format or date object)
            limit: Maximum number of snapshots to return (default 288 = 5-min intervals for 24h)

        Returns: List of dicts with snapshot data, newest first
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        elif isinstance(target_date, str):
            target_date = datetime.fromisoformat(target_date).date()

        date_str = target_date.isoformat()

        conn = db_connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            SELECT
                id,
                snapshot_timestamp,
                occupancy_count,
                capacity_limit,
                capacity_warning,
                daily_entries,
                daily_exits,
                created_at
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) = ?
            ORDER BY snapshot_timestamp DESC
            LIMIT ?
            """,
            (date_str, limit),
        )

        rows = c.fetchall()
        conn.close()

        return [
            {
                "id": row[0],
                "snapshot_timestamp": row[1],
                "occupancy_count": row[2],
                "capacity_limit": row[3],
                "capacity_warning": bool(row[4]),
                "daily_entries": row[5],
                "daily_exits": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]

    def get_daily_summary(self, target_date: date | None = None) -> dict:
        """
        Get end-of-day occupancy summary (final counts and anomalies).

        Returns dict with:
        - date_str
        - daily_entries
        - daily_exits
        - net_occupancy
        - peak_occupancy
        - capacity_warnings_count
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()
        elif isinstance(target_date, str):
            target_date = datetime.fromisoformat(target_date).date()

        date_str = target_date.isoformat()

        conn = db_connect(self.db_path)
        c = conn.cursor()

        # Final occupancy count for the day
        c.execute(
            """
            SELECT
                SUM(CASE WHEN entered_at IS NOT NULL THEN 1 ELSE 0 END) as entries,
                SUM(CASE WHEN exited_at IS NOT NULL THEN 1 ELSE 0 END) as exits
            FROM recognition_events
            WHERE (entered_at IS NOT NULL OR exited_at IS NOT NULL)
              AND DATE(COALESCE(entered_at, exited_at)) = ?
            """,
            (date_str,),
        )
        row = c.fetchone()
        daily_entries = row[0] or 0
        daily_exits = row[1] or 0

        # Peak occupancy from snapshots
        c.execute(
            """
            SELECT MAX(occupancy_count)
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) = ?
            """,
            (date_str,),
        )
        peak_occupancy = c.fetchone()[0] or 0

        # Count capacity warnings
        c.execute(
            """
            SELECT COUNT(*)
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) = ? AND capacity_warning = 1
            """,
            (date_str,),
        )
        capacity_warnings_count = c.fetchone()[0] or 0

        conn.close()

        net_occupancy = daily_entries - daily_exits

        return {
            "date_str": date_str,
            "daily_entries": daily_entries,
            "daily_exits": daily_exits,
            "net_occupancy": net_occupancy,
            "peak_occupancy": peak_occupancy,
            "capacity_warnings_count": capacity_warnings_count,
            "tracked_state": self.get_daily_state(target_date),
        }
