"""Occupancy Service - Manages library occupancy tracking and capacity monitoring.

Responsibilities:
- Calculate current occupancy (entries - exits for today)
- Generate occupancy snapshots at regular intervals
- Check occupancy against capacity limits
- Detect capacity breaches and anomalies
- Provide historical occupancy data for analytics
"""
from __future__ import annotations

from datetime import datetime, date, timezone, timedelta

from db import connect as db_connect

DEFAULT_CAPACITY_LIMIT = 300
MIN_CAPACITY_LIMIT = 50
MAX_CAPACITY_LIMIT = 2000


def resolve_capacity_limit(
    db_path: str,
    default: int = DEFAULT_CAPACITY_LIMIT,
    minimum: int = MIN_CAPACITY_LIMIT,
    maximum: int = MAX_CAPACITY_LIMIT,
) -> int:
    """Resolve runtime capacity limit from app_settings with safe fallback bounds."""
    try:
        fallback = int(default)
    except (TypeError, ValueError):
        fallback = DEFAULT_CAPACITY_LIMIT

    if minimum > maximum:
        minimum, maximum = maximum, minimum

    resolved = fallback
    conn = None
    try:
        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key = %s", ("max_occupancy",))
        row = c.fetchone()
        if row and row[0] is not None:
            resolved = int(str(row[0]).strip())
    except Exception:
        resolved = fallback
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return max(int(minimum), min(int(maximum), int(resolved)))


class OccupancyService:
    """Manages occupancy calculations and snapshots."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @staticmethod
    def _coerce_target_date(target_date: date | datetime | str | None) -> date:
        if target_date is None:
            return datetime.now(timezone.utc).date()
        if isinstance(target_date, datetime):
            return target_date.date()
        if isinstance(target_date, date):
            return target_date
        text = str(target_date).strip()
        if not text:
            return datetime.now(timezone.utc).date()
        return datetime.fromisoformat(text).date()

    @staticmethod
    def _normalize_event_type(event_type: str | int | None) -> str:
        text = str(event_type or "").strip().lower()
        if text in {"entry", "1"}:
            return "entry"
        if text in {"exit", "2"}:
            return "exit"
        return "unknown"

    def record_event(self, event_type: str | int, captured_at: datetime | None = None) -> dict:
        """Record a single recognition event into the daily occupancy state."""
        event_time = captured_at or datetime.now(timezone.utc)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        event_time = event_time.astimezone(timezone.utc)
        state_date = event_time.date().isoformat()
        normalized_event_type = self._normalize_event_type(event_type)

        daily_entries = 1 if normalized_event_type == "entry" else 0
        daily_exits = 1 if normalized_event_type == "exit" else 0

        conn = db_connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
            VALUES (%s, %s, %s, %s)
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
            WHERE state_date = %s
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
            WHERE state_date = %s
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

        # Count entry events for the target date
        c.execute(
            """
            SELECT COUNT(*) FROM recognition_events
            WHERE event_type = 'entry'
              AND DATE(COALESCE(captured_at, ingested_at)) = %s
            """,
            (date_str,),
        )
        daily_entries = c.fetchone()[0] or 0

        # Count exit events for the target date
        c.execute(
            """
            SELECT COUNT(*) FROM recognition_events
            WHERE event_type = 'exit'
              AND DATE(COALESCE(captured_at, ingested_at)) = %s
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

    def get_current_occupancy(self, capacity_limit: int, warning_threshold: float = 0.90) -> dict:
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
        threshold = min(1.0, max(0.0, float(warning_threshold)))
        capacity_warning = occupancy_ratio >= threshold

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

    def create_snapshot(self, capacity_limit: int, warning_threshold: float = 0.90) -> None:
        """
        Create a point-in-time snapshot of current occupancy and save to database.

        This should be called periodically (e.g., every 5 minutes) and whenever
        capacity status changes.
        """
        occ_data = self.get_current_occupancy(capacity_limit, warning_threshold=warning_threshold)

        conn = db_connect(self.db_path)
        c = conn.cursor()

        now_utc = datetime.now(timezone.utc)

        c.execute(
            """
            INSERT INTO occupancy_snapshots
            (snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
             daily_entries, daily_exits, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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
            WHERE DATE(snapshot_timestamp) = %s
            ORDER BY snapshot_timestamp DESC
            LIMIT %s
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
                SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) as entries,
                SUM(CASE WHEN event_type = 'exit' THEN 1 ELSE 0 END) as exits
            FROM recognition_events
            WHERE DATE(COALESCE(captured_at, ingested_at)) = %s
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
            WHERE DATE(snapshot_timestamp) = %s
            """,
            (date_str,),
        )
        peak_occupancy = c.fetchone()[0] or 0

        # Count capacity warnings
        c.execute(
            """
            SELECT COUNT(*)
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) = %s AND capacity_warning = 1
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

    def get_daily_report(self, target_date: date | datetime | str | None = None) -> dict:
        """Return occupancy analytics for a single day."""
        target_day = self._coerce_target_date(target_date)
        date_str = target_day.isoformat()

        conn = db_connect(self.db_path)
        c = conn.cursor()

        c.execute(
            """
            SELECT
                SUM(CASE WHEN event_type = 'entry' THEN 1 ELSE 0 END) AS total_entries,
                SUM(CASE WHEN event_type = 'exit' THEN 1 ELSE 0 END) AS total_exits
            FROM recognition_events
            WHERE DATE(COALESCE(captured_at, ingested_at)) = %s
            """,
            (date_str,),
        )
        total_row = c.fetchone() or (0, 0)
        total_entries = int(total_row[0] or 0)
        total_exits = int(total_row[1] or 0)

        c.execute(
            """
            SELECT
                COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized') AS user_type,
                SUM(CASE WHEN re.event_type = 'entry' THEN 1 ELSE 0 END) AS entries,
                SUM(CASE WHEN re.event_type = 'exit' THEN 1 ELSE 0 END) AS exits
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE DATE(COALESCE(re.captured_at, re.ingested_at)) = %s
            GROUP BY COALESCE(NULLIF(TRIM(u.user_type), ''), 'unrecognized')
            """,
            (date_str,),
        )
        by_user_type = {
            "enrolled": {"entries": 0, "exits": 0},
            "visitor": {"entries": 0, "exits": 0},
            "unrecognized": {"entries": 0, "exits": 0},
        }
        for user_type, entries, exits in c.fetchall():
            normalized = str(user_type or "").strip().lower() or "unrecognized"
            current = by_user_type.setdefault(normalized, {"entries": 0, "exits": 0})
            current["entries"] = int(entries or 0)
            current["exits"] = int(exits or 0)

        c.execute(
            """
            SELECT
                COALESCE(NULLIF(TRIM(u.course), ''), 'Unknown') AS program,
                SUM(CASE WHEN re.event_type = 'entry' THEN 1 ELSE 0 END) AS entries,
                SUM(CASE WHEN re.event_type = 'exit' THEN 1 ELSE 0 END) AS exits
            FROM recognition_events re
            LEFT JOIN users u ON re.user_id = u.user_id
            WHERE DATE(COALESCE(re.captured_at, re.ingested_at)) = %s
            GROUP BY COALESCE(NULLIF(TRIM(u.course), ''), 'Unknown')
            HAVING
                SUM(CASE WHEN re.event_type = 'entry' THEN 1 ELSE 0 END) > 0
                OR SUM(CASE WHEN re.event_type = 'exit' THEN 1 ELSE 0 END) > 0
            ORDER BY entries DESC, exits DESC, program ASC
            """,
            (date_str,),
        )
        by_program: dict[str, dict[str, int]] = {}
        for program, entries, exits in c.fetchall():
            by_program[str(program or "Unknown")] = {
                "entries": int(entries or 0),
                "exits": int(exits or 0),
            }

        c.execute(
            """
            SELECT hour_slot, COUNT(*) AS event_count
            FROM (
                SELECT EXTRACT(HOUR FROM COALESCE(captured_at, ingested_at))::int AS hour_slot
                FROM recognition_events
                WHERE DATE(COALESCE(captured_at, ingested_at)) = %s
            ) hourly_events
            GROUP BY hour_slot
            ORDER BY event_count DESC, hour_slot ASC
            LIMIT 1
            """,
            (date_str,),
        )
        peak_hour_row = c.fetchone()
        if peak_hour_row:
            start_hour = int(peak_hour_row[0] or 0)
            end_hour = (start_hour + 1) % 24
            peak_hour = f"{start_hour:02d}:00-{end_hour:02d}:00"
        else:
            peak_hour = "N/A"

        c.execute(
            """
            SELECT MAX(occupancy_count)
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) = %s
            """,
            (date_str,),
        )
        peak_occupancy = int((c.fetchone() or [0])[0] or 0)
        if peak_occupancy <= 0:
            tracked = self.get_daily_state(target_day)
            if tracked:
                peak_occupancy = max(peak_occupancy, int(tracked.get("occupancy_count", 0)))
        if peak_occupancy <= 0:
            peak_occupancy = max(0, total_entries - total_exits)

        conn.close()

        return {
            "date": date_str,
            "total_entries": total_entries,
            "total_exits": total_exits,
            "by_user_type": by_user_type,
            "by_program": by_program,
            "peak_hour": peak_hour,
            "peak_occupancy": peak_occupancy,
        }

    def get_occupancy_trends(self, days: int = 7) -> dict:
        """Return daily occupancy trend aggregates over the requested period."""
        day_count = max(1, min(int(days or 7), 365))
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=day_count - 1)

        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT
                DATE(snapshot_timestamp) AS snapshot_date,
                AVG(occupancy_count) AS avg_occupancy,
                MAX(occupancy_count) AS peak_occupancy,
                SUM(CASE WHEN capacity_warning = 1 THEN 1 ELSE 0 END) AS capacity_breaches
            FROM occupancy_snapshots
            WHERE DATE(snapshot_timestamp) BETWEEN %s AND %s
            GROUP BY DATE(snapshot_timestamp)
            ORDER BY snapshot_date ASC
            """,
            (start_str, end_str),
        )
        snapshot_rows = c.fetchall()

        c.execute(
            """
            SELECT state_date, daily_entries, daily_exits
            FROM daily_occupancy_state
            WHERE state_date BETWEEN %s AND %s
            """,
            (start_str, end_str),
        )
        state_rows = c.fetchall()
        conn.close()

        snapshot_map = {
            str(row[0]): {
                "avg_occupancy": float(row[1] or 0.0),
                "peak_occupancy": int(row[2] or 0),
                "capacity_breaches": int(row[3] or 0),
            }
            for row in snapshot_rows
        }
        state_map = {
            str(row[0]): max(0, int(row[1] or 0) - int(row[2] or 0))
            for row in state_rows
        }

        series = []
        for offset in range(day_count):
            day_value = start_date + timedelta(days=offset)
            day_str = day_value.isoformat()
            metrics = snapshot_map.get(day_str)
            if metrics is None:
                fallback_occupancy = int(state_map.get(day_str, 0))
                metrics = {
                    "avg_occupancy": float(fallback_occupancy),
                    "peak_occupancy": fallback_occupancy,
                    "capacity_breaches": 0,
                }
            series.append(
                {
                    "date": day_str,
                    "avg_occupancy": round(float(metrics["avg_occupancy"]), 1),
                    "peak_occupancy": int(metrics["peak_occupancy"]),
                    "capacity_breaches": int(metrics["capacity_breaches"]),
                }
            )

        return {
            "period": f"{day_count} days",
            "days": day_count,
            "start_date": start_str,
            "end_date": end_str,
            "data": series,
        }

    def adjust_occupancy(self, adjustment: int, reason: str, admin_id: int | None = None) -> dict:
        """Apply a manual drift correction to today's tracked occupancy state."""
        if int(adjustment) == 0:
            raise ValueError("`adjustment` must be non-zero.")

        now = datetime.now(timezone.utc)
        state_date = now.date().isoformat()
        delta = int(adjustment)
        entry_delta = max(delta, 0)
        exit_delta = max(-delta, 0)

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(state_date) DO UPDATE SET
                daily_entries = daily_occupancy_state.daily_entries + excluded.daily_entries,
                daily_exits = daily_occupancy_state.daily_exits + excluded.daily_exits,
                updated_at = excluded.updated_at
            """,
            (state_date, entry_delta, exit_delta, now),
        )
        c.execute(
            """
            SELECT daily_entries, daily_exits
            FROM daily_occupancy_state
            WHERE state_date = %s
            """,
            (state_date,),
        )
        row = c.fetchone()
        daily_entries = int(row[0] or 0) if row else 0
        daily_exits = int(row[1] or 0) if row else 0

        # Clamp impossible negative occupancy by normalizing exits to entries.
        if daily_exits > daily_entries:
            daily_exits = daily_entries
            c.execute(
                """
                UPDATE daily_occupancy_state
                SET daily_exits = %s, updated_at = %s
                WHERE state_date = %s
                """,
                (daily_exits, now, state_date),
            )

        occupancy_count = max(0, daily_entries - daily_exits)
        message = (
            f"Manual occupancy adjustment applied (delta={delta})"
            f"{' by admin_id=' + str(admin_id) if admin_id is not None else ''}: {reason}"
        )
        c.execute(
            """
            INSERT INTO occupancy_alerts (
                alert_type, level, message, occupancy_count, capacity_limit, occupancy_ratio,
                is_active, state_date, dismissed_at, dismissed_by, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "manual_adjustment",
                "info",
                message,
                occupancy_count,
                0,
                0.0,
                False,
                state_date,
                now,
                str(admin_id) if admin_id is not None else "system",
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return {
            "state_date": state_date,
            "new_occupancy": occupancy_count,
            "daily_entries": daily_entries,
            "daily_exits": daily_exits,
            "audit_logged": True,
        }

    def reconcile_day(self, target_date: date | None = None, drift_threshold: int = 5) -> dict:
        """Compare tracked daily state vs canonical recognition events and alert on drift."""
        if target_date is None:
            target_date = datetime.now(timezone.utc).date() - timedelta(days=1)

        date_str = target_date.isoformat()
        tracked = self.get_daily_state(target_date) or {
            "daily_entries": 0,
            "daily_exits": 0,
            "occupancy_count": 0,
        }
        actual = self.calculate_occupancy(target_date)
        entry_drift = int(actual["daily_entries"]) - int(tracked["daily_entries"])
        exit_drift = int(actual["daily_exits"]) - int(tracked["daily_exits"])
        net_drift = int(actual["occupancy_count"]) - int(tracked["occupancy_count"])
        exceeds = abs(net_drift) > int(drift_threshold)

        if exceeds:
            now = datetime.now(timezone.utc)
            message = (
                f"Nightly occupancy drift detected on {date_str}: "
                f"net_drift={net_drift} (actual={actual['occupancy_count']}, tracked={tracked['occupancy_count']}). "
                "Manual correction is recommended."
            )
            conn = db_connect(self.db_path)
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO occupancy_alerts (
                    alert_type, level, message, occupancy_count, capacity_limit, occupancy_ratio,
                    is_active, state_date, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "occupancy_drift",
                    "warning",
                    message,
                    int(tracked["occupancy_count"]),
                    0,
                    0.0,
                    True,
                    date_str,
                    now,
                    now,
                ),
            )
            conn.commit()
            conn.close()

        return {
            "date": date_str,
            "tracked_entries": int(tracked["daily_entries"]),
            "tracked_exits": int(tracked["daily_exits"]),
            "actual_entries": int(actual["daily_entries"]),
            "actual_exits": int(actual["daily_exits"]),
            "entry_drift": entry_drift,
            "exit_drift": exit_drift,
            "net_drift": net_drift,
            "threshold": int(drift_threshold),
            "alerted": exceeds,
        }
