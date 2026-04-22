from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
import unittest

SOURCE_PATH = Path("routes/routes.py")


class DashboardDateKeyNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE_PATH))
        target = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_normalize_date_key"
            ),
            None,
        )
        if target is None:
            raise AssertionError("_normalize_date_key function not found in routes/routes.py")

        module = ast.Module(body=[target], type_ignores=[])
        namespace = {"date": date, "datetime": datetime}
        exec(compile(module, filename=str(SOURCE_PATH), mode="exec"), namespace)
        cls.normalize_date_key = staticmethod(namespace["_normalize_date_key"])

    def test_handles_python_date(self) -> None:
        self.assertEqual(self.normalize_date_key(date(2026, 4, 22)), "2026-04-22")

    def test_handles_python_datetime(self) -> None:
        self.assertEqual(
            self.normalize_date_key(datetime(2026, 4, 22, 9, 45, 12)),
            "2026-04-22",
        )

    def test_handles_timestamp_text(self) -> None:
        self.assertEqual(
            self.normalize_date_key("2026-04-22 09:45:12"),
            "2026-04-22",
        )

    def test_handles_empty_values(self) -> None:
        self.assertIsNone(self.normalize_date_key(""))
        self.assertIsNone(self.normalize_date_key(None))


if __name__ == "__main__":
    unittest.main()
