from __future__ import annotations

import unittest

from db import _translate_sqlite_to_postgres


class PostgresSqlTranslationTests(unittest.TestCase):
    def test_translates_sqlite_relative_day_now(self) -> None:
        query = "SELECT * FROM recognition_log WHERE DATE(timestamp) >= DATE('now', '-13 days')"
        translated = _translate_sqlite_to_postgres(query)
        self.assertIn("CURRENT_DATE - INTERVAL '13 days'", translated)

    def test_translates_sqlite_start_of_month_relative_months(self) -> None:
        query = "SELECT * FROM recognition_log WHERE DATE(timestamp) >= date('now','start of month','-5 months')"
        translated = _translate_sqlite_to_postgres(query)
        self.assertIn("date_trunc('month', CURRENT_DATE) - INTERVAL '5 months'", translated)

    def test_translates_strftime_hour_with_alias_and_cast(self) -> None:
        query = "SELECT CAST(strftime('%H', r.timestamp) AS INTEGER) AS hour FROM recognition_log r"
        translated = _translate_sqlite_to_postgres(query)
        self.assertIn("EXTRACT(HOUR FROM r.timestamp)::int", translated)

    def test_translates_pragma_table_info(self) -> None:
        query = "PRAGMA table_info(users)"
        translated = _translate_sqlite_to_postgres(query)
        self.assertIn("information_schema.columns", translated)
        self.assertIn("table_name = 'users'", translated)


if __name__ == "__main__":
    unittest.main()
