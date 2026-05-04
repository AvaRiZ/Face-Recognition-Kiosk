from __future__ import annotations

from db import connect as db_connect
from db import table_columns
from services.versioning_service import ensure_version_settings


REQUIRED_CANONICAL_TABLES = (
    "recognition_events",
    "user_embeddings",
    "app_settings",
    "daily_occupancy_state",
    "occupancy_snapshots",
    "occupancy_alerts",
    "user_registrations",
)


def init_canonical_schema(db_path: str) -> None:
    conn = db_connect(db_path)

    missing_tables = [name for name in REQUIRED_CANONICAL_TABLES if not table_columns(conn, name)]
    recognition_event_columns = table_columns(conn, "recognition_events")
    user_columns = table_columns(conn, "users")
    conn.close()

    if missing_tables:
        raise RuntimeError(
            "PostgreSQL schema is missing required canonical tables "
            f"{missing_tables}. Run `alembic upgrade head` before starting the app."
        )
    if "event_type" not in recognition_event_columns:
        raise RuntimeError(
            "PostgreSQL schema is missing canonical recognition_events.event_type. "
            "Run `alembic upgrade head` before starting the app."
        )
    missing_user_columns = [name for name in ("user_type", "flow_type") if name not in user_columns]
    if missing_user_columns:
        raise RuntimeError(
            "PostgreSQL schema is missing required users columns "
            f"{missing_user_columns}. Run `alembic upgrade head` before starting the app."
        )

    ensure_version_settings(db_path)
