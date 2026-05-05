from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import types

from flask import Flask

from core.config import AppConfig
from core.state import AppStateManager

if "flask_socketio" not in sys.modules:
    class _SocketIoStub:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            return None

    sys.modules["flask_socketio"] = types.SimpleNamespace(SocketIO=_SocketIoStub)

try:
    from routes.routes import create_routes_blueprint
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency in CI.
    create_routes_blueprint = None


def _normalize_sql(query: str) -> str:
    return " ".join(str(query).split()).lower()


def _profile_value(row: dict, key: str) -> str:
    return str(row.get(key) or "")


def _filter_rows(source_rows: list[dict], query: str, params: list) -> list[dict]:
    normalized = _normalize_sql(query)

    if "archived_at is not null" in normalized:
        rows = [row for row in source_rows if row.get("archived_at")]
    else:
        rows = [row for row in source_rows if not row.get("archived_at")]

    idx = 0
    if "lower(coalesce(name, '')) like %s" in normalized:
        raw = str(params[idx] or "").lower()
        needle = raw.replace("%", "")
        idx += 2
        rows = [
            row
            for row in rows
            if needle in _profile_value(row, "name").lower() or needle in _profile_value(row, "sr_code").lower()
        ]

    if "course = %s" in normalized:
        program = str(params[idx] or "")
        rows = [row for row in rows if _profile_value(row, "course") == program]

    return rows


def _sort_rows(rows: list[dict], query: str) -> list[dict]:
    normalized = _normalize_sql(query)
    key_name = "created_at"
    reverse = True

    if "order by name asc" in normalized:
        key_name = "name"
        reverse = False
    elif "order by name desc" in normalized:
        key_name = "name"
        reverse = True
    elif "order by created_at asc" in normalized:
        key_name = "created_at"
        reverse = False
    elif "order by created_at desc" in normalized:
        key_name = "created_at"
        reverse = True
    elif "order by last_updated asc" in normalized:
        key_name = "last_updated"
        reverse = False
    elif "order by last_updated desc" in normalized:
        key_name = "last_updated"
        reverse = True
    elif "order by archived_at asc" in normalized:
        key_name = "archived_at"
        reverse = False
    elif "order by archived_at desc" in normalized:
        key_name = "archived_at"
        reverse = True

    return sorted(
        rows,
        key=lambda row: (_profile_value(row, key_name).lower(), int(row.get("user_id") or 0)),
        reverse=reverse,
    )


class _FakeProfilesCursor:
    def __init__(self, store: dict):
        self.store = store
        self._rows = []
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = _normalize_sql(query)
        values = list(params or [])
        self._rows = []
        self.rowcount = 0

        if normalized.startswith(
            "select coalesce(sum(case when archived_at is null then 1 else 0 end), 0) as active_count"
        ):
            active = sum(1 for row in self.store["rows"] if not row.get("archived_at"))
            archived = sum(1 for row in self.store["rows"] if row.get("archived_at"))
            self._rows = [(active, archived)]
            return

        if normalized.startswith("select count(*) from users where"):
            filtered = _filter_rows(self.store["rows"], normalized, values)
            self._rows = [(len(filtered),)]
            return

        if normalized.startswith("select user_id, name, sr_code, gender, course as program, created_at, last_updated, archived_at from users where"):
            limit = int(values[-2])
            offset = int(values[-1])
            filtered = _filter_rows(self.store["rows"], normalized, values[:-2])
            sorted_rows = _sort_rows(filtered, normalized)
            page_rows = sorted_rows[offset : offset + limit]
            self._rows = [
                (
                    row["user_id"],
                    row["name"],
                    row["sr_code"],
                    row["gender"],
                    row["course"],
                    row["created_at"],
                    row["last_updated"],
                    row["archived_at"],
                )
                for row in page_rows
            ]
            return

        if normalized.startswith("select distinct course from users where"):
            filtered = _filter_rows(self.store["rows"], normalized, [])
            unique = sorted(
                {
                    _profile_value(row, "course")
                    for row in filtered
                    if _profile_value(row, "course").strip()
                }
            )
            self._rows = [(value,) for value in unique]
            return

        if normalized.startswith("select user_id from users where user_id = %s"):
            user_id = int(values[0])
            exists = any(int(row.get("user_id") or 0) == user_id for row in self.store["rows"])
            self._rows = [(user_id,)] if exists else []
            return

        if normalized.startswith("update users set name = %s, sr_code = %s, gender = %s, course = %s, last_updated = current_timestamp where user_id = %s"):
            user_id = int(values[4])
            for row in self.store["rows"]:
                if int(row.get("user_id") or 0) == user_id:
                    row["name"] = values[0]
                    row["sr_code"] = values[1]
                    row["gender"] = values[2]
                    row["course"] = values[3]
                    row["last_updated"] = "2026-05-05 10:00:00"
                    self.rowcount = 1
                    break
            return

        if normalized.startswith("select name, sr_code, archived_at from users where user_id = %s"):
            user_id = int(values[0])
            row = next((entry for entry in self.store["rows"] if int(entry.get("user_id") or 0) == user_id), None)
            self._rows = [(row["name"], row["sr_code"], row["archived_at"])] if row else []
            return

        if normalized.startswith("delete from users where user_id = %s"):
            user_id = int(values[0])
            before = len(self.store["rows"])
            self.store["rows"] = [row for row in self.store["rows"] if int(row.get("user_id") or 0) != user_id]
            self.rowcount = 1 if len(self.store["rows"]) < before else 0
            return

        raise AssertionError(f"Unexpected SQL in test double: {normalized}")

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


class _FakeProfilesConnection:
    def __init__(self, store: dict):
        self.store = store

    def cursor(self):
        return _FakeProfilesCursor(self.store)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _RepositoryStub:
    def __init__(self, store: dict):
        self.store = store

    def get_user_by_sr_code(self, sr_code):
        row = next((entry for entry in self.store["rows"] if str(entry.get("sr_code")) == str(sr_code)), None)
        if not row:
            return None
        return SimpleNamespace(id=int(row["user_id"]))

    def get_user_by_id(self, user_id):
        row = next((entry for entry in self.store["rows"] if int(entry.get("user_id") or 0) == int(user_id)), None)
        if not row:
            return None
        return SimpleNamespace(id=int(row["user_id"]))

    def save_user(self, _user):
        return 999


@unittest.skipIf(create_routes_blueprint is None, "Route blueprint dependencies are unavailable.")
class ProfileManagementApiTests(unittest.TestCase):
    def _build_client(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)

        config = AppConfig()
        config.db_path = str(temp_path / "profiles.db")
        config.base_save_dir = str(temp_path / "faces")
        state = AppStateManager(config)

        self.store = {
            "rows": [
                {
                    "user_id": 1,
                    "name": "Alice Cruz",
                    "sr_code": "23-00001",
                    "gender": "Female",
                    "course": "BSCS",
                    "created_at": "2026-01-01 08:00:00",
                    "last_updated": "2026-01-02 08:00:00",
                    "archived_at": None,
                },
                {
                    "user_id": 2,
                    "name": "Brian Gomez",
                    "sr_code": "23-00002",
                    "gender": "Male",
                    "course": "BSIT",
                    "created_at": "2026-01-03 08:00:00",
                    "last_updated": "2026-01-03 12:00:00",
                    "archived_at": "2026-02-01 10:00:00",
                },
                {
                    "user_id": 3,
                    "name": "Alicia Dela Cruz",
                    "sr_code": "23-00003",
                    "gender": "Female",
                    "course": "BSCS",
                    "created_at": "2026-01-04 08:00:00",
                    "last_updated": "2026-01-04 10:00:00",
                    "archived_at": None,
                },
            ]
        }
        self.removed_embeddings = []
        repo = _RepositoryStub(self.store)

        app = Flask(__name__)
        app.secret_key = "test-secret"
        deps = {
            "config": config,
            "db_path": config.db_path,
            "base_save_dir": config.base_save_dir,
            "repository": repo,
            "worker_runtime_attached": True,
            "get_thresholds": state.get_thresholds,
            "set_thresholds": state.set_thresholds,
            "get_user_count": lambda: 0,
            "get_registration_state": lambda: state.registration_state,
            "capture_registration_sample": state.capture_registration_sample,
            "get_current_registration_pose": state.get_current_registration_pose,
            "get_registration_progress": state.get_registration_progress,
            "is_registration_ready": state.is_registration_ready,
            "expire_registration_session_if_needed": state.expire_registration_session_if_needed,
            "reset_database_state": state.reset_database_state,
            "reset_registration_state": state.reset_registration_state,
            "start_web_registration_session": state.start_web_registration_session,
            "cancel_web_registration_session": state.cancel_web_registration_session,
            "set_registration_status_reason": state.set_registration_status_reason,
            "clear_registration_status_reason": state.clear_registration_status_reason,
            "complete_registration": state.complete_registration,
            "remove_user_embedding": lambda user_id: self.removed_embeddings.append(int(user_id)),
            "replace_user": state.replace_user,
            "render_markdown_as_html": lambda _path: "",
            "pause_detection": lambda: None,
            "resume_detection": lambda: None,
            "detection_paused": lambda: False,
            "stream_status": lambda: {"state": "live", "message": "Camera stream active."},
            "yolo_model": None,
            "yolo_device": "cpu",
        }

        with patch("routes.routes.init_imported_logs_table", return_value=None), patch(
            "routes.routes.ensure_version_settings", return_value=None
        ):
            app.register_blueprint(create_routes_blueprint(deps))
        return app.test_client()

    @staticmethod
    def _set_admin_session(client) -> None:
        with client.session_transaction() as sess:
            sess["staff_id"] = 1
            sess["username"] = "admin"
            sess["role"] = "library_admin"

    def test_profiles_list_filters_active_search_and_pagination_metadata(self) -> None:
        client = self._build_client()
        self._set_admin_session(client)

        with patch("routes.routes.db_connect", return_value=_FakeProfilesConnection(self.store)):
            response = client.get("/api/profiles?status=active&q=ali&page=1&page_size=1&sort=name_asc")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload.get("total"), 2)
        self.assertEqual(payload.get("page"), 1)
        self.assertEqual(payload.get("page_size"), 1)
        self.assertEqual(payload.get("total_pages"), 2)
        self.assertEqual(payload.get("counts", {}).get("active"), 2)
        self.assertEqual(payload.get("counts", {}).get("archived"), 1)
        self.assertEqual(len(payload.get("rows", [])), 1)
        self.assertEqual(payload["rows"][0]["name"], "Alice Cruz")

    def test_profiles_list_returns_only_archived_when_requested(self) -> None:
        client = self._build_client()
        self._set_admin_session(client)

        with patch("routes.routes.db_connect", return_value=_FakeProfilesConnection(self.store)):
            response = client.get("/api/profiles?status=archived")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        rows = payload.get("rows", [])
        self.assertEqual(payload.get("total"), 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("sr_code"), "23-00002")
        self.assertTrue(rows[0].get("archived_at"))

    def test_delete_profile_rejects_active_records(self) -> None:
        client = self._build_client()
        self._set_admin_session(client)

        with patch("routes.routes.db_connect", return_value=_FakeProfilesConnection(self.store)):
            response = client.delete("/api/profiles/1")

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertIn("must be archived", payload.get("message", ""))

    def test_delete_profile_allows_archived_records(self) -> None:
        client = self._build_client()
        self._set_admin_session(client)

        with patch("routes.routes.db_connect", return_value=_FakeProfilesConnection(self.store)), patch(
            "routes.routes.bump_profiles_version", return_value=None
        ), patch("routes.routes.log_action", return_value=None):
            response = client.delete("/api/profiles/2")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("user_id"), 2)
        self.assertEqual(self.removed_embeddings, [2])
        remaining_ids = sorted(int(row["user_id"]) for row in self.store["rows"])
        self.assertEqual(remaining_ids, [1, 3])

    def test_profile_update_keeps_sr_code_uniqueness_validation(self) -> None:
        client = self._build_client()
        self._set_admin_session(client)

        with patch("routes.routes.db_connect", return_value=_FakeProfilesConnection(self.store)):
            response = client.put(
                "/api/profiles/3",
                json={
                    "name": "Dela Cruz, Alicia",
                    "sr_code": "23-00001",
                    "gender": "Female",
                    "program": "BSCS",
                },
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertIn("already exists", payload.get("message", ""))


if __name__ == "__main__":
    unittest.main()
