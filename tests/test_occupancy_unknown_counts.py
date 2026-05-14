from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from services.occupancy_service import (
    OccupancyService,
    is_occupancy_counted_decision,
    occupancy_decision_filter_sql,
)


class _FakeCursor:
    def __init__(self, state: dict):
        self.state = state
        self.row = None

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).lower().split())
        self.state.setdefault("sql", []).append(str(sql))

        if "select state_date, daily_entries, daily_exits, updated_at" in normalized:
            self.row = (
                self.state["state_date"],
                self.state["daily_entries"],
                self.state["daily_exits"],
                self.state["updated_at"],
            )
            return

        if "select count(*) from recognition_events" in normalized and "event_type = 'entry'" in normalized:
            self.row = (self.state["canonical_entries"],)
            return

        if "select count(*) from recognition_events" in normalized and "event_type = 'exit'" in normalized:
            self.row = (self.state["canonical_exits"],)
            return

        if "insert into daily_occupancy_state" in normalized and "excluded.daily_entries" in normalized:
            self.state["state_date"] = params[0]
            self.state["daily_entries"] = int(params[1])
            self.state["daily_exits"] = int(params[2])
            self.state["updated_at"] = params[3]
            self.row = None
            return

        self.row = None

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, state: dict):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        pass

    def close(self):
        pass


class OccupancyUnknownCountsTests(unittest.TestCase):
    def test_unknown_decision_is_counted_for_occupancy(self):
        self.assertTrue(is_occupancy_counted_decision("allowed"))
        self.assertTrue(is_occupancy_counted_decision("unknown"))
        self.assertTrue(is_occupancy_counted_decision(""))
        self.assertFalse(is_occupancy_counted_decision("denied"))

        self.assertIn("'unknown'", occupancy_decision_filter_sql())

    def test_current_occupancy_resyncs_to_unknown_inclusive_canonical_counts(self):
        today = date.today().isoformat()
        state = {
            "state_date": today,
            "daily_entries": 0,
            "daily_exits": 0,
            "updated_at": datetime.now(timezone.utc),
            "canonical_entries": 3,
            "canonical_exits": 1,
        }

        with patch("services.occupancy_service.db_connect", side_effect=lambda _db_path: _FakeConnection(state)):
            payload = OccupancyService("postgresql://test").get_current_occupancy(10)

        self.assertEqual(payload["daily_entries"], 3)
        self.assertEqual(payload["daily_exits"], 1)
        self.assertEqual(payload["occupancy_count"], 2)

        combined_sql = "\n".join(state["sql"])
        self.assertIn("IN ('allowed', 'unknown')", combined_sql)
        self.assertIn('NOT LIKE \'%%"revoked": true%%\'', combined_sql)


if __name__ == "__main__":
    unittest.main()
