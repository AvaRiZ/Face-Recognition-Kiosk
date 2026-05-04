from __future__ import annotations

import unittest

from db import _normalize_query_for_postgres


class PostgresQueryNormalizationTests(unittest.TestCase):
    def test_translates_relative_day_now(self) -> None:
        query = "SELECT * FROM recognition_events WHERE DATE(captured_at) >= DATE('now', '-13 days')"
        translated = _normalize_query_for_postgres(query)
        self.assertIn("CURRENT_DATE - INTERVAL '13 days'", translated)

    def test_translates_start_of_month_relative_months(self) -> None:
        query = "SELECT * FROM recognition_events WHERE DATE(captured_at) >= date('now','start of month','-5 months')"
        translated = _normalize_query_for_postgres(query)
        self.assertIn("date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'", translated)

    def test_translates_hour_extract_cast(self) -> None:
        query = "SELECT CAST(strftime('%H', r.captured_at) AS INTEGER) AS hour FROM recognition_events r"
        translated = _normalize_query_for_postgres(query)
        self.assertIn("EXTRACT(HOUR FROM r.captured_at)::int", translated)

    def test_translates_qmark_placeholders(self) -> None:
        query = "SELECT * FROM users WHERE sr_code = ? AND archived_at IS ?"
        translated = _normalize_query_for_postgres(query)
        self.assertEqual(
            translated,
            "SELECT * FROM users WHERE sr_code = %s AND archived_at IS %s",
        )


if __name__ == "__main__":
    unittest.main()
