from __future__ import annotations

from pathlib import Path
import re
import unittest


class VisitReportEntryOnlyContractTests(unittest.TestCase):
    def test_monthly_daily_visits_query_counts_entry_events_only(self) -> None:
        source = Path("routes/routes.py").read_text(encoding="utf-8")
        match = re.search(
            r"def _monthly_daily_visits_data\(.*?c\.execute\(\s*\"\"\"(?P<query>.*?FROM recognition_events re.*?)\"\"\"",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "Could not find monthly daily visits query")

        query = " ".join(match.group("query").lower().split())
        self.assertIn("from recognition_events re", query)
        self.assertIn("coalesce(nullif(trim(re.event_type), ''), 'entry') = 'entry'", query)


if __name__ == "__main__":
    unittest.main()
