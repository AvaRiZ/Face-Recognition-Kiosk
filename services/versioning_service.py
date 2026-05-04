from __future__ import annotations

from db import connect as db_connect
from db import table_columns


def ensure_version_settings(db_path: str) -> None:
    conn = db_connect(db_path)
    c = conn.cursor()
    if not table_columns(conn, "app_settings"):
        conn.close()
        raise RuntimeError(
            "PostgreSQL schema is missing `app_settings`. "
            "Run `alembic upgrade head` before starting the app."
        )
    c.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('profiles_version', '1')
        ON CONFLICT(key) DO NOTHING
        """
    )
    c.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('settings_version', '1')
        ON CONFLICT(key) DO NOTHING
        """
    )
    conn.commit()
    conn.close()


def _read_int_setting(db_path: str, key: str, default: int = 1) -> int:
    ensure_version_settings(db_path)
    conn = db_connect(db_path)
    c = conn.cursor()
    c.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
    row = c.fetchone()
    conn.close()
    if not row or row[0] is None:
        return int(default)
    try:
        return int(str(row[0]).strip())
    except Exception:
        return int(default)


def _write_int_setting(db_path: str, key: str, value: int) -> None:
    ensure_version_settings(db_path)
    conn = db_connect(db_path)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, str(int(value))),
    )
    conn.commit()
    conn.close()


def get_profiles_version(db_path: str) -> int:
    return _read_int_setting(db_path, "profiles_version", default=1)


def get_settings_version(db_path: str) -> int:
    return _read_int_setting(db_path, "settings_version", default=1)


def bump_profiles_version(db_path: str) -> int:
    current = get_profiles_version(db_path)
    next_version = current + 1
    _write_int_setting(db_path, "profiles_version", next_version)
    return next_version


def bump_settings_version(db_path: str) -> int:
    current = get_settings_version(db_path)
    next_version = current + 1
    _write_int_setting(db_path, "settings_version", next_version)
    return next_version
