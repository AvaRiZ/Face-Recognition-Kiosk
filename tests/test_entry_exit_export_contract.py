from __future__ import annotations

from pathlib import Path
import unittest


class EntryExitExportContractTests(unittest.TestCase):
    def test_entry_exit_excel_template_exists(self) -> None:
        template_path = Path("static/report_templates/LIBRARY USERS VISITS TEMPLATE.xlsx")
        self.assertTrue(template_path.exists(), msg=f"Missing export template: {template_path}")

    def test_backend_export_uses_xlsx_template_response(self) -> None:
        source = Path("routes/routes.py").read_text(encoding="utf-8")
        self.assertIn("LIBRARY USERS VISITS TEMPLATE.xlsx", source)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", source)
        self.assertIn("library_entry_logs_", source)

    def test_frontend_entry_exit_export_downloads_xlsx(self) -> None:
        source = Path("frontend/src/pages/EntryExitLogs.jsx").read_text(encoding="utf-8")
        self.assertIn(".xlsx", source)
        self.assertIn("Export Excel", source)
        self.assertNotIn("Export CSV", source)

    def test_stale_reentry_auto_exit_metadata_feeds_export_pairing(self) -> None:
        internal_source = Path("routes/internal_routes.py").read_text(encoding="utf-8")
        repository_source = Path("database/repository.py").read_text(encoding="utf-8")

        for source in (internal_source, repository_source):
            self.assertIn('"auto_reason": "missed_exit_reentry"', source)
            self.assertIn('"trigger_entry_event_id": event_id', source)

        self.assertIn('_upsert_daily_occupancy_state(c, auto_exit_at, "exit")', internal_source)
        self.assertIn('open_sessions[-1]["exit_timestamp"] = time_text', Path("routes/routes.py").read_text(encoding="utf-8"))

    def test_presence_gate_queries_are_date_scoped_for_daily_reset(self) -> None:
        internal_source = Path("routes/internal_routes.py").read_text(encoding="utf-8")
        repository_source = Path("database/repository.py").read_text(encoding="utf-8")

        for source in (internal_source, repository_source):
            self.assertIn("DATE(COALESCE(captured_at, ingested_at)) = %s", source)
            self.assertIn("_presence_event_date", source)


if __name__ == "__main__":
    unittest.main()
