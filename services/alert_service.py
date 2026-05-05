from __future__ import annotations

from datetime import datetime, timezone

from db import connect as db_connect


class AlertService:
    """Manage threshold-based occupancy alerts."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def create_capacity_reached_alert(self, *, occupancy_count: int, capacity_limit: int) -> dict:
        now = datetime.now(timezone.utc)
        ratio = (float(occupancy_count) / float(capacity_limit)) if capacity_limit > 0 else 0.0
        state_date = now.date().isoformat()
        message = (
            f"Capacity reached: occupancy {int(occupancy_count)}/{int(capacity_limit)}. "
            "New entries are blocked until occupancy decreases."
        )

        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            SELECT id FROM occupancy_alerts
            WHERE alert_type = %s AND state_date = %s AND dismissed_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            ("capacity_reached", state_date),
        )
        row = c.fetchone()

        if row:
            alert_id = int(row[0])
            c.execute(
                """
                UPDATE occupancy_alerts
                SET level = %s, message = %s, occupancy_count = %s, capacity_limit = %s,
                    occupancy_ratio = %s, is_active = TRUE, updated_at = %s
                WHERE id = %s
                """,
                ("full", message, int(occupancy_count), int(capacity_limit), float(ratio), now, alert_id),
            )
        else:
            c.execute(
                """
                INSERT INTO occupancy_alerts (
                    alert_type, level, message, occupancy_count, capacity_limit, occupancy_ratio,
                    is_active, state_date, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
                RETURNING id
                """,
                ("capacity_reached", "full", message, int(occupancy_count), int(capacity_limit), float(ratio), state_date, now, now),
            )
            row = c.fetchone()
            alert_id = int(row[0]) if row else 0
            if alert_id <= 0:
                c.execute(
                    "SELECT id FROM occupancy_alerts WHERE alert_type = %s AND state_date = %s ORDER BY id DESC LIMIT 1",
                    ("capacity_reached", state_date),
                )
                fallback = c.fetchone()
                alert_id = int(fallback[0]) if fallback else 0

        conn.commit()
        conn.close()
        return {"alert_id": alert_id, "alert_type": "capacity_reached", "level": "full", "message": message}

    def list_alerts(self, *, active_only: bool = True, limit: int = 50) -> list[dict]:
        conn = db_connect(self.db_path)
        c = conn.cursor()
        if active_only:
            c.execute(
                """
                SELECT id, alert_type, level, message, occupancy_count, capacity_limit, occupancy_ratio,
                       is_active, state_date, dismissed_at, dismissed_by, created_at, updated_at
                FROM occupancy_alerts
                WHERE dismissed_at IS NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
        else:
            c.execute(
                """
                SELECT id, alert_type, level, message, occupancy_count, capacity_limit, occupancy_ratio,
                       is_active, state_date, dismissed_at, dismissed_by, created_at, updated_at
                FROM occupancy_alerts
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
        rows = c.fetchall()
        conn.close()

        return [
            {
                "id": int(row[0]),
                "alert_type": row[1],
                "level": row[2],
                "message": row[3],
                "occupancy_count": int(row[4] or 0),
                "capacity_limit": int(row[5] or 0),
                "occupancy_ratio": float(row[6] or 0.0),
                "is_active": bool(row[7]),
                "state_date": row[8],
                "dismissed_at": row[9],
                "dismissed_by": row[10],
                "created_at": row[11],
                "updated_at": row[12],
            }
            for row in rows
        ]

    def dismiss_alert(self, alert_id: int, dismissed_by: str) -> bool:
        now = datetime.now(timezone.utc)
        conn = db_connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """
            UPDATE occupancy_alerts
            SET dismissed_at = %s, dismissed_by = %s, is_active = FALSE, updated_at = %s
            WHERE id = %s AND dismissed_at IS NULL
            """,
            (now, str(dismissed_by or "unknown"), now, int(alert_id)),
        )
        changed = (c.rowcount or 0) > 0
        conn.commit()
        conn.close()
        return changed

