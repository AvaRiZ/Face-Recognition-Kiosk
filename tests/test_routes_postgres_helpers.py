from __future__ import annotations

import ast
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import struct
import unittest

SOURCE_PATH = Path("routes/routes.py")


class RoutesPostgresHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE_PATH))

        target_names = {
            "_normalize_date_key",
            "_normalize_timestamp_for_json",
            "_coerce_confidence",
        }
        discovered: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in target_names:
                discovered.setdefault(node.name, node)

        if len(discovered) != len(target_names):
            missing = target_names - set(discovered)
            raise AssertionError(f"Missing expected helper(s) in routes/routes.py: {sorted(missing)}")

        module = ast.Module(
            body=[discovered[name] for name in sorted(discovered)],
            type_ignores=[],
        )
        namespace = {"date": date, "datetime": datetime, "struct": struct}
        exec(compile(module, filename=str(SOURCE_PATH), mode="exec"), namespace)
        cls.normalize_date_key = staticmethod(namespace["_normalize_date_key"])
        cls.normalize_timestamp = staticmethod(namespace["_normalize_timestamp_for_json"])
        cls.coerce_confidence = staticmethod(namespace["_coerce_confidence"])

    def test_normalize_timestamp_handles_datetime(self) -> None:
        value = datetime(2026, 4, 22, 14, 30, 5)
        self.assertEqual(self.normalize_timestamp(value), "2026-04-22 14:30:05")

    def test_normalize_timestamp_handles_date(self) -> None:
        self.assertEqual(self.normalize_timestamp(date(2026, 4, 22)), "2026-04-22")

    def test_normalize_timestamp_handles_blank(self) -> None:
        self.assertEqual(self.normalize_timestamp("   ", default="-"), "-")

    def test_coerce_confidence_accepts_numeric_string(self) -> None:
        self.assertAlmostEqual(self.coerce_confidence("0.93"), 0.93)

    def test_coerce_confidence_accepts_decimal(self) -> None:
        self.assertAlmostEqual(self.coerce_confidence(Decimal("0.87")), 0.87)

    def test_normalize_date_key_handles_datetime(self) -> None:
        value = datetime(2026, 4, 22, 14, 30, 5)
        self.assertEqual(self.normalize_date_key(value), "2026-04-22")


if __name__ == "__main__":
    unittest.main()
