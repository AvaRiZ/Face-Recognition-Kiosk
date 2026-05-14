from __future__ import annotations

import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from core.config import AppConfig

realtime_stub = sys.modules.setdefault("app.realtime", types.ModuleType("app.realtime"))
realtime_stub.emit_analytics_update = lambda *args, **kwargs: None
realtime_stub.emit_capacity_threshold_alert = lambda *args, **kwargs: None
realtime_stub.emit_unrecognized_detection = lambda *args, **kwargs: None

from routes.routes import create_routes_blueprint


class _FakeCursor:
    def __init__(self):
        self._row = (0,)
        self._rows = []

    @staticmethod
    def _is_entry_only_query(normalized: str) -> bool:
        return (
            "coalesce(nullif(trim(event_type), ''), 'entry') = 'entry'" in normalized
            or "coalesce(nullif(trim(re.event_type), ''), 'entry') = 'entry'" in normalized
        )

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).lower().split())
        today = date.today()
        entry_only = self._is_entry_only_query(normalized)
        self._row = (0,)
        self._rows = []

        if normalized.startswith("create index"):
            self._row = None
            return

        if "select value from app_settings where key" in normalized:
            self._row = ("300",)
            return

        if "from daily_occupancy_state" in normalized and "select state_date" in normalized:
            self._row = (today.isoformat(), 0, 0, datetime.now(timezone.utc))
            return

        if "select count(*) from users" in normalized:
            self._row = (42,)
            return

        if "select date(captured_at) as day" in normalized:
            self._rows = [(today, 6 if entry_only else 8)]
            self._row = None
            return

        if "select confidence" in normalized:
            self._rows = [(0.9,), (0.5,), (0.8,), (0.4,)]
            self._row = None
            return

        if "group by event_type" in normalized:
            self._rows = [("entry", 6), ("exit", 2)]
            self._row = None
            return

        if " as user_type, count(*) as count" in normalized:
            counts_unknown_decisions = "coalesce(re.decision, 'allowed') = 'unknown'" in normalized
            unrecognized_entry_count = 2 if counts_unknown_decisions else 0
            unrecognized_all_count = 3 if counts_unknown_decisions else 0
            self._rows = (
                [
                    ("enrolled", 4),
                    ("visitor", 3),
                    ("unrecognized", unrecognized_entry_count),
                    ("staff", 99),
                ]
                if entry_only
                else [
                    ("enrolled", 6),
                    ("visitor", 5),
                    ("unrecognized", unrecognized_all_count),
                    ("staff", 99),
                ]
            )
            self._row = None
            return

        if (
            "extract(hour from captured_at)::int as hour" in normalized
            and "group by hour" in normalized
            and "case extract(dow" not in normalized
        ):
            self._rows = [(9, 4), (14, 2)] if entry_only else [(9, 4), (14, 2), (16, 3)]
            self._row = None
            return

        if "case extract(dow" in normalized:
            self._rows = (
                [(today.weekday(), 9, 4), (today.weekday(), 14, 2)]
                if entry_only
                else [(today.weekday(), 9, 4), (today.weekday(), 14, 2), (today.weekday(), 16, 3)]
            )
            self._row = None
            return

        if "select re.id," in normalized:
            event_time = datetime.now(timezone.utc)
            self._rows = [
                (12, "Ada Lovelace", "23-12345", "enrolled", 0.92, "entry", event_time, "allowed", "{}"),
                (13, "Grace Hopper", "24-54321", "visitor", 0.81, "exit", event_time, "allowed", "{}"),
                (14, "", "", "unrecognized", 0.0, "entry", event_time, "unknown", "{}"),
            ]
            self._row = None
            return

        if "select e.id," in normalized:
            event_time = datetime.now(timezone.utc)
            self._rows = [
                (
                    15,
                    "revoked-unknown-test",
                    None,
                    "",
                    "",
                    "unrecognized",
                    0.0,
                    "entry",
                    event_time,
                    "unknown",
                    '{"identity_user_type":"unrecognized","revoked": true}',
                ),
                (
                    14,
                    "unknown-test",
                    None,
                    "",
                    "",
                    "unrecognized",
                    0.0,
                    "entry",
                    event_time,
                    "unknown",
                    '{"identity_user_type":"unrecognized"}',
                ),
                (
                    12,
                    "allowed-test",
                    1,
                    "Ada Lovelace",
                    "23-12345",
                    "enrolled",
                    0.92,
                    "entry",
                    event_time,
                    "allowed",
                    "{}",
                ),
            ]
            self._row = None
            return

        if "to_char(captured_at, 'yyyy-mm')" in normalized:
            self._rows = [(today.strftime("%Y-%m"), 6 if entry_only else 8)]
            self._row = None
            return

        if "count(distinct user_id)" in normalized:
            self._row = (5 if entry_only else 7,)
            return

        if "select count(*)" in normalized:
            self._row = (6 if entry_only else 8,)
            return

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def cursor(self):
        return _FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


class DashboardMonitoringRefreshContractTests(unittest.TestCase):
    def _build_client(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        config = AppConfig()

        patches = [
            patch("routes.routes.db_connect", side_effect=lambda _db_path: _FakeConnection()),
            patch("services.occupancy_service.db_connect", side_effect=lambda _db_path: _FakeConnection()),
            patch("routes.routes.table_columns", return_value={"key", "value"}),
            patch("routes.routes.ensure_version_settings", return_value=None),
            patch("routes.routes.log_action", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        app.register_blueprint(
            create_routes_blueprint(
                {
                    "config": config,
                    "db_path": "postgresql://test",
                    "get_thresholds": lambda: (0.75, 0.8),
                    "set_thresholds": lambda *_args, **_kwargs: None,
                    "repository": None,
                }
            )
        )
        client = app.test_client()
        with client.session_transaction() as session:
            session["staff_id"] = 1
            session["username"] = "admin"
            session["role"] = "library_staff"
        return client

    def test_preset_dashboard_returns_staff_monitoring_contract(self):
        client = self._build_client()
        today = date.today()
        week_start = today - timedelta(days=today.weekday())

        response = client.get(
            f"/api/dashboard?filter=last_30_days&heatmap_week_start={week_start.isoformat()}"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["filter_key"], "last_30_days")
        self.assertEqual(payload["filter_label"], "Last 30 Days")
        self.assertEqual(payload["filter_days"], 30)
        self.assertEqual(payload["heatmap_week_start_date"], week_start.isoformat())
        self.assertEqual(payload["heatmap_week_end_date"], (week_start + timedelta(days=6)).isoformat())
        self.assertIn(" - ", payload["heatmap_week_label"])
        self.assertIn("peak_pattern_summary", payload)
        self.assertEqual(payload["peak_pattern_summary"]["busiest_hour"]["label"], "9 AM")
        self.assertEqual(payload["total_logs"], 6)
        self.assertEqual(payload["total_entries"], 6)
        self.assertEqual(payload["total_exits"], 2)
        self.assertEqual(payload["avg_confidence"], 65)
        self.assertEqual(payload["low_confidence_count"], 2)
        self.assertEqual(payload["unique_visitors"], 5)
        self.assertEqual(payload["daily_visitors"][-1]["count"], 6)
        self.assertEqual(payload["peak_hours"][9], 4)
        self.assertEqual(payload["peak_hours"][16], 0)

        labels = [item["label"] for item in payload["user_type_distribution"]]
        self.assertEqual(labels, ["Students", "Visitors", "Unrecognized"])
        self.assertNotIn("Staff", labels)
        user_type_counts = {item["label"]: item["count"] for item in payload["user_type_distribution"]}
        self.assertEqual(user_type_counts["Students"], 4)
        self.assertEqual(user_type_counts["Visitors"], 3)
        self.assertEqual(user_type_counts["Unrecognized"], 2)

        self.assertEqual(payload["recent_entries"][0]["event_type"], "entry")
        self.assertEqual(payload["recent_entries"][1]["event_type"], "exit")
        self.assertEqual(payload["recent_entries"][0]["conf_pct"], 92)
        unrecognized_recent = next(
            item for item in payload["recent_entries"] if item["status"] == "unknown"
        )
        self.assertEqual(unrecognized_recent["name"], "Unrecognized User")
        self.assertEqual(unrecognized_recent["sr_code"], "N/A")
        self.assertEqual(unrecognized_recent["user_type"], "unrecognized")

        today_row = payload["weekly_heatmap"][today.weekday()]
        self.assertEqual(sum(today_row["values"]), 6)
        self.assertEqual(today_row["values"][9 - 7], 4)
        self.assertEqual(today_row["values"][16 - 7], 0)
        self.assertEqual(payload["monthly_visitors"][-1]["count"], 6)

    def test_custom_dashboard_filter_is_removed(self):
        client = self._build_client()

        response = client.get("/api/dashboard?filter=custom&start_date=2026-05-01&end_date=2026-05-01")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["filter_key"], "last_14_days")
        self.assertEqual(payload["filter_label"], "Last 14 Days")

    def test_unrecognized_events_api_returns_display_identity(self):
        client = self._build_client()

        response = client.get("/api/events?type=unrecognized")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["name"], "Unrecognized User")
        self.assertEqual(row["sr_code"], "N/A")
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["user_type"], "unrecognized")
        self.assertEqual(row["event_type"], "entry")

    def test_heatmap_week_rejects_invalid_date_format(self):
        client = self._build_client()

        response = client.get("/api/dashboard?filter=today&heatmap_week_start=not-a-date")

        self.assertEqual(response.status_code, 400)
        self.assertIn("heatmap_week_start", response.get_json()["message"])

    def test_frontend_dashboard_source_uses_staff_monitoring_controls(self):
        source = Path("frontend/src/pages/Dashboard.jsx").read_text(encoding="utf-8")
        styles = Path("frontend/src/pages/Dashboard.css").read_text(encoding="utf-8")

        self.assertNotIn("Custom Range", source)
        self.assertNotIn("dashboard-custom-range-controls", source)
        self.assertIn("dashboard-filter-chips", source)
        self.assertIn("dashboard-recent-tags", source)
        self.assertIn("dashboard-heatmap-total", source)
        self.assertIn("heatmap_week_start", source)
        self.assertIn("Peak traffic patterns", source)
        self.assertNotIn("Live occupancy trend", source)
        self.assertIn("dashboard-filter-chip", styles)
        self.assertIn("dashboard-heatmap-total", styles)


if __name__ == "__main__":
    unittest.main()
