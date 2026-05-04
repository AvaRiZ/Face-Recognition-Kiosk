from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from db import connect as db_connect
from services.occupancy_service import OccupancyService


def _create_tables(db_path: str) -> None:
    conn = db_connect(db_path)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            sr_code TEXT,
            course TEXT,
            user_type TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE recognition_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            user_id INTEGER,
            sr_code TEXT,
            decision TEXT NOT NULL,
            event_type TEXT NOT NULL,
            confidence REAL,
            captured_at TIMESTAMP,
            ingested_at TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE occupancy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_timestamp TIMESTAMP NOT NULL,
            occupancy_count INTEGER NOT NULL,
            capacity_limit INTEGER NOT NULL,
            capacity_warning BOOLEAN NOT NULL DEFAULT 0,
            daily_entries INTEGER NOT NULL DEFAULT 0,
            daily_exits INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE daily_occupancy_state (
            state_date TEXT PRIMARY KEY,
            daily_entries INTEGER NOT NULL DEFAULT 0,
            daily_exits INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


class OccupancyAnalyticsContractTests(unittest.TestCase):
    def test_daily_report_returns_phase5_shape(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "occupancy_contract.db")
        _create_tables(db_path)

        report_date = date(2026, 5, 2)
        date_str = report_date.isoformat()

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO users (name, sr_code, course, user_type)
            VALUES ('Alice', 'SR001', 'Computer Science', 'enrolled')
            """
        )
        c.execute(
            """
            INSERT INTO users (name, sr_code, course, user_type)
            VALUES ('Guest', 'V001', 'External Relations', 'visitor')
            """
        )
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence, captured_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-1",
                1,
                "SR001",
                "allowed",
                "entry",
                0.93,
                f"{date_str} 08:10:00",
                f"{date_str} 08:10:01",
            ),
        )
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence, captured_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-2",
                1,
                "SR001",
                "allowed",
                "exit",
                0.92,
                f"{date_str} 09:20:00",
                f"{date_str} 09:20:01",
            ),
        )
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence, captured_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-3",
                2,
                "V001",
                "allowed",
                "entry",
                0.88,
                f"{date_str} 10:15:00",
                f"{date_str} 10:15:01",
            ),
        )
        c.execute(
            """
            INSERT INTO recognition_events (
                event_id, user_id, sr_code, decision, event_type, confidence, captured_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-4",
                None,
                None,
                "unknown",
                "entry",
                0.42,
                f"{date_str} 11:05:00",
                f"{date_str} 11:05:01",
            ),
        )
        c.execute(
            """
            INSERT INTO occupancy_snapshots (
                snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
                daily_entries, daily_exits, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"{date_str} 12:00:00", 4, 300, 0, 4, 1, f"{date_str} 12:00:00"),
        )
        c.execute(
            """
            INSERT INTO occupancy_snapshots (
                snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
                daily_entries, daily_exits, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"{date_str} 13:00:00", 6, 300, 0, 6, 1, f"{date_str} 13:00:00"),
        )
        conn.commit()
        conn.close()

        service = OccupancyService(db_path)
        report = service.get_daily_report(report_date)

        self.assertEqual(report["date"], date_str)
        self.assertEqual(report["total_entries"], 3)
        self.assertEqual(report["total_exits"], 1)
        self.assertEqual(report["peak_occupancy"], 6)
        self.assertIn("peak_hour", report)
        self.assertTrue(report["peak_hour"].endswith(":00"))
        self.assertEqual(report["by_user_type"]["enrolled"]["entries"], 1)
        self.assertEqual(report["by_user_type"]["enrolled"]["exits"], 1)
        self.assertEqual(report["by_user_type"]["visitor"]["entries"], 1)
        self.assertEqual(report["by_user_type"]["unrecognized"]["entries"], 1)
        self.assertEqual(report["by_program"]["Computer Science"]["entries"], 1)

    def test_occupancy_trends_falls_back_to_daily_state_when_snapshots_missing(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "occupancy_trends.db")
        _create_tables(db_path)

        today = datetime.now(timezone.utc).date()
        day_1 = today - timedelta(days=2)
        day_2 = today - timedelta(days=1)
        day_3 = today

        conn = db_connect(db_path)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO occupancy_snapshots (
                snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
                daily_entries, daily_exits, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"{day_1.isoformat()} 08:00:00", 12, 100, 0, 20, 8, f"{day_1.isoformat()} 08:00:00"),
        )
        c.execute(
            """
            INSERT INTO occupancy_snapshots (
                snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
                daily_entries, daily_exits, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"{day_3.isoformat()} 09:00:00", 18, 100, 1, 30, 12, f"{day_3.isoformat()} 09:00:00"),
        )
        c.execute(
            """
            INSERT INTO daily_occupancy_state (state_date, daily_entries, daily_exits, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (day_2.isoformat(), 15, 5, f"{day_2.isoformat()} 12:00:00"),
        )
        conn.commit()
        conn.close()

        service = OccupancyService(db_path)
        payload = service.get_occupancy_trends(days=3)

        self.assertEqual(payload["period"], "3 days")
        self.assertEqual(len(payload["data"]), 3)

        by_date = {entry["date"]: entry for entry in payload["data"]}
        self.assertEqual(by_date[day_1.isoformat()]["peak_occupancy"], 12)
        self.assertEqual(by_date[day_2.isoformat()]["avg_occupancy"], 10.0)
        self.assertEqual(by_date[day_2.isoformat()]["peak_occupancy"], 10)
        self.assertEqual(by_date[day_2.isoformat()]["capacity_breaches"], 0)
        self.assertEqual(by_date[day_3.isoformat()]["capacity_breaches"], 1)

    def test_occupancy_snapshot_history_has_required_fields_for_trend_analysis(self) -> None:
        """Verify snapshot history API returns all fields needed for frontend trend calculation."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "trend_analysis.db")
        _create_tables(db_path)

        report_date = date(2026, 5, 2)
        date_str = report_date.isoformat()

        conn = db_connect(db_path)
        c = conn.cursor()
        # Insert 6 snapshots simulating a rising trend (5-min intervals = 30 min span)
        for i in range(6):
            c.execute(
                """
                INSERT INTO occupancy_snapshots (
                    snapshot_timestamp, occupancy_count, capacity_limit, capacity_warning,
                    daily_entries, daily_exits, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{date_str} {8 + i // 4:02d}:{5 * i % 60:02d}:00",
                    20 + (i * 2),  # Rising: 20, 22, 24, 26, 28, 30
                    300,
                    0,
                    20 + i,
                    0,
                    f"{date_str} {8 + i // 4:02d}:{5 * i % 60:02d}:00",
                ),
            )
        conn.commit()
        conn.close()

        service = OccupancyService(db_path)
        history = service.get_history(report_date, limit=288)

        # Verify all required fields are present for trend calculation
        self.assertGreater(len(history), 0)
        for snapshot in history:
            self.assertIn("snapshot_timestamp", snapshot)
            self.assertIn("occupancy_count", snapshot)
            self.assertIn("capacity_limit", snapshot)
            self.assertIn("capacity_warning", snapshot)
            self.assertIn("daily_entries", snapshot)
            self.assertIn("daily_exits", snapshot)
            # Verify types are correct for frontend
            self.assertIsInstance(snapshot["snapshot_timestamp"], str)
            self.assertIsInstance(snapshot["occupancy_count"], int)
            self.assertIsInstance(snapshot["capacity_limit"], int)
            self.assertIsInstance(snapshot["capacity_warning"], (int, bool))

    def test_occupancy_history_empty_case_when_no_snapshots(self) -> None:
        """Verify empty snapshot history is handled gracefully."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = str(Path(temp_dir.name) / "empty_history.db")
        _create_tables(db_path)

        report_date = date(2026, 5, 2)

        service = OccupancyService(db_path)
        history = service.get_history(report_date, limit=288)

        # Empty history should return empty list, not None or error
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 0)


if __name__ == "__main__":
    unittest.main()
