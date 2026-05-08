from __future__ import annotations

from pathlib import Path
import unittest


class ProgramMonthlyExportContractTests(unittest.TestCase):
    def test_program_monthly_excel_template_exists(self) -> None:
        template_path = Path("static/report_templates/MONTHLY PROGRAM VISIT TEMPLATE.xlsx")
        self.assertTrue(template_path.exists(), msg=f"Missing export template: {template_path}")

    def test_backend_program_monthly_export_uses_xlsx(self) -> None:
        source = Path("routes/routes.py").read_text(encoding="utf-8")
        self.assertIn("MONTHLY PROGRAM VISIT TEMPLATE.xlsx", source)
        self.assertIn("program_monthly_visits_", source)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", source)

    def test_frontend_program_monthly_export_downloads_xlsx(self) -> None:
        source = Path("frontend/src/pages/ProgramMonthlyVisits.jsx").read_text(encoding="utf-8")
        self.assertIn(".xlsx", source)
        self.assertIn("Export Excel", source)
        self.assertNotIn("Export CSV", source)


if __name__ == "__main__":
    unittest.main()
