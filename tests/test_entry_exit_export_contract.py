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


if __name__ == "__main__":
    unittest.main()
