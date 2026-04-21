from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_FILES = [
    path
    for path in PROJECT_ROOT.rglob("*.py")
    if ".venv" not in path.parts and "__pycache__" not in path.parts
]


def _collect_route_endpoints() -> set[str]:
    endpoints: set[str] = set()

    for path in PYTHON_FILES:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        blueprint_vars: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "Blueprint":
                continue
            if not node.value.args:
                continue
            arg0 = node.value.args[0]
            if not isinstance(arg0, ast.Constant) or not isinstance(arg0.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    blueprint_vars[target.id] = arg0.value

        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for dec in fn.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr != "route":
                    continue

                endpoint_name = None
                for kw in dec.keywords:
                    if kw.arg == "endpoint" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        endpoint_name = kw.value.value
                        break
                if endpoint_name is None:
                    endpoint_name = fn.name

                # Blueprint route: bp.route(...)
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id in blueprint_vars:
                    bp_name = blueprint_vars[dec.func.value.id]
                    endpoints.add(f"{bp_name}.{endpoint_name}")
                    continue

                # App route: app.route(...)
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                    endpoints.add(endpoint_name)

    return endpoints


def _collect_url_for_literals() -> list[tuple[str, int, str]]:
    calls: list[tuple[str, int, str]] = []

    for path in PYTHON_FILES:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        rel_path = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            is_url_for = isinstance(func, ast.Name) and func.id == "url_for"
            if not is_url_for and isinstance(func, ast.Attribute):
                is_url_for = func.attr == "url_for"
            if not is_url_for or not node.args:
                continue

            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                calls.append((rel_path, node.lineno, arg0.value))

    return calls


class EndpointReferenceTests(unittest.TestCase):
    def test_all_literal_url_for_endpoints_exist(self) -> None:
        endpoints = _collect_route_endpoints()
        calls = _collect_url_for_literals()

        missing = [(path, line, endpoint) for path, line, endpoint in calls if endpoint not in endpoints]
        self.assertEqual(
            [],
            missing,
            msg="Missing endpoints in url_for literals:\n"
            + "\n".join(f"- {path}:{line} -> {endpoint}" for path, line, endpoint in missing),
        )


if __name__ == "__main__":
    unittest.main()
