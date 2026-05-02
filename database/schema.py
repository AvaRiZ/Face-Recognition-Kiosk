from __future__ import annotations

from db import connect as db_connect
from db import table_columns
from services.versioning_service import ensure_version_settings


def init_canonical_schema(db_path: str) -> None:
    conn = db_connect(db_path)
    if getattr(conn, "dialect", "sqlite") == "postgres":
        required_tables = (
            "recognition_events",
            "user_embeddings",
            "app_settings",
            "daily_occupancy_state",
            "occupancy_snapshots",
            "occupancy_alerts",
            "user_registrations",
        )
        missing = [name for name in required_tables if not table_columns(conn, name)]
        conn.close()
        if missing:
            raise RuntimeError(
                "PostgreSQL schema is missing required canonical tables "
                f"{missing}. Run `alembic upgrade head` before starting the app."
            )
        ensure_version_settings(db_path)
        return

    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS recognition_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            user_id INTEGER,
            sr_code TEXT,
            decision TEXT NOT NULL CHECK(decision IN ('allowed', 'denied', 'unknown')),
            confidence REAL,
            primary_confidence REAL,
            secondary_confidence REAL,
            primary_distance REAL,
            secondary_distance REAL,
            face_quality REAL,
            method TEXT,
            captured_at TIMESTAMP,
            entered_at TIMESTAMP,
            exited_at TIMESTAMP,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payload_json TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE SET NULL,
            CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
            CHECK (primary_confidence IS NULL OR (primary_confidence >= 0.0 AND primary_confidence <= 1.0)),
            CHECK (secondary_confidence IS NULL OR (secondary_confidence >= 0.0 AND secondary_confidence <= 1.0)),
            CHECK (face_quality IS NULL OR (face_quality >= 0.0 AND face_quality <= 1.0))
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_captured_at
        ON recognition_events(captured_at)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_entered_at
        ON recognition_events(entered_at)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_exited_at
        ON recognition_events(exited_at)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_user_id
        ON recognition_events(user_id)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_ingested_at_desc
        ON recognition_events(ingested_at DESC)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_sr_code
        ON recognition_events(sr_code)
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_embeddings (
            embedding_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            model_name TEXT NOT NULL,
            embedding BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_embeddings_user_model
        ON user_embeddings(user_id, model_name)
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS occupancy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_timestamp TIMESTAMP NOT NULL,
            occupancy_count INTEGER NOT NULL,
            capacity_limit INTEGER NOT NULL,
            capacity_warning BOOLEAN NOT NULL DEFAULT 0,
            daily_entries INTEGER NOT NULL DEFAULT 0,
            daily_exits INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_occupancy_snapshots_timestamp_desc
        ON occupancy_snapshots(snapshot_timestamp DESC)
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_occupancy_state (
            state_date TEXT PRIMARY KEY,
            daily_entries INTEGER NOT NULL DEFAULT 0,
            daily_exits INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS occupancy_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            occupancy_count INTEGER NOT NULL,
            capacity_limit INTEGER NOT NULL,
            occupancy_ratio REAL NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            state_date TEXT NOT NULL,
            dismissed_at TIMESTAMP,
            dismissed_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_user_columns = table_columns(conn, "users")
    if "user_type" not in existing_user_columns:
        c.execute("ALTER TABLE users ADD COLUMN user_type TEXT DEFAULT 'enrolled'")
    if "flow_type" not in existing_user_columns:
        c.execute("ALTER TABLE users ADD COLUMN flow_type TEXT DEFAULT 'auto_entry'")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_id TEXT,
            registration_type TEXT NOT NULL,
            flow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            performed_by TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE SET NULL
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_registrations_created_at_desc
        ON user_registrations(created_at DESC)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_registrations_event_id
        ON user_registrations(event_id)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_occupancy_alerts_created_at_desc
        ON occupancy_alerts(created_at DESC)
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_occupancy_alerts_active_date
        ON occupancy_alerts(alert_type, state_date, dismissed_at)
        """
    )
    conn.commit()
    conn.close()

    ensure_version_settings(db_path)
