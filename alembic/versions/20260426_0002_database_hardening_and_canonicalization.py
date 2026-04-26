"""database hardening and canonicalization

Revision ID: 20260426_0002
Revises: 20260420_0001
Create Date: 2026-04-26
"""

from __future__ import annotations

import pickle
from typing import Iterable

import numpy as np
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260426_0002"
down_revision = "20260420_0001"
branch_labels = None
depends_on = None


def _constraint_exists(bind, table: str, constraint: str) -> bool:
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE n.nspname = 'public'
              AND t.relname = :table_name
              AND c.conname = :constraint_name
            LIMIT 1
            """
        ),
        {"table_name": table, "constraint_name": constraint},
    ).fetchone()
    return bool(row)


def _column_type(bind, table: str, column: str) -> str | None:
    row = bind.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name AND column_name = :column_name
            """
        ),
        {"table_name": table, "column_name": column},
    ).fetchone()
    if not row:
        return None
    return str(row[0] or "").strip().lower() or None


def _normalize_vectors(values: Iterable) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for value in values:
        try:
            arr = np.asarray(value, dtype=np.float32)
        except Exception:
            continue
        if arr.ndim == 1 and arr.size > 0:
            out.append(arr)
        elif arr.ndim == 2:
            for row in arr:
                if row.size > 0:
                    out.append(np.asarray(row, dtype=np.float32))
    return out


def _legacy_blob_to_embeddings(blob) -> dict[str, list[np.ndarray]]:
    if blob is None:
        return {}
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if not isinstance(blob, bytes) or not blob:
        return {}

    try:
        payload = pickle.loads(blob)
    except Exception:
        return {}

    if isinstance(payload, dict):
        normalized: dict[str, list[np.ndarray]] = {}
        for model_name, vectors in payload.items():
            if isinstance(vectors, np.ndarray):
                vectors = [vectors]
            if not isinstance(vectors, (list, tuple)):
                vectors = [vectors]
            rows = _normalize_vectors(vectors)
            if rows:
                normalized[str(model_name)] = rows
        return normalized

    if isinstance(payload, np.ndarray):
        payload = [payload]
    if isinstance(payload, (list, tuple)):
        rows = _normalize_vectors(payload)
        if rows:
            return {"legacy": rows}
    return {}


def _backfill_user_embeddings(bind) -> None:
    has_table = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='user_embeddings'
            )
            """
        )
    ).scalar_one()
    if not has_table:
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT user_id, embeddings
            FROM users
            WHERE embeddings IS NOT NULL
            """
        )
    ).fetchall()

    for user_id, legacy_blob in rows:
        exists = bind.execute(
            sa.text("SELECT 1 FROM user_embeddings WHERE user_id = :user_id LIMIT 1"),
            {"user_id": int(user_id)},
        ).fetchone()
        if exists:
            continue

        embeddings_by_model = _legacy_blob_to_embeddings(legacy_blob)
        if not embeddings_by_model:
            continue

        insert_rows = []
        for model_name, vectors in embeddings_by_model.items():
            for vector in vectors:
                insert_rows.append(
                    {
                        "user_id": int(user_id),
                        "model_name": str(model_name),
                        "embedding": vector.astype(np.float32, copy=False).tobytes(),
                    }
                )

        if insert_rows:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO user_embeddings (user_id, model_name, embedding)
                    VALUES (:user_id, :model_name, :embedding)
                    """
                ),
                insert_rows,
            )


def _convert_column_to_timestamptz(bind, table: str, column: str) -> None:
    dtype = _column_type(bind, table, column)
    if not dtype:
        return

    if dtype == "timestamp with time zone":
        return

    if dtype in {"character varying", "text"}:
        op.execute(
            sa.text(
                f"""
                ALTER TABLE {table}
                ALTER COLUMN {column} TYPE TIMESTAMPTZ
                USING (
                    CASE
                        WHEN {column} IS NULL OR BTRIM({column}) = '' THEN NULL
                        WHEN BTRIM({column}) ~ '(Z|[+-][0-9]{{2}}(:?[0-9]{{2}})?)$' THEN BTRIM({column})::timestamptz
                        ELSE BTRIM({column})::timestamp AT TIME ZONE 'UTC'
                    END
                )
                """
            )
        )
        return

    if dtype == "timestamp without time zone":
        op.execute(
            sa.text(
                f"""
                ALTER TABLE {table}
                ALTER COLUMN {column} TYPE TIMESTAMPTZ
                USING ({column} AT TIME ZONE 'UTC')
                """
            )
        )


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT,
            sr_code TEXT UNIQUE,
            gender TEXT,
            course TEXT,
            embeddings BYTEA NOT NULL DEFAULT '\\x'::bytea,
            image_paths TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            archived_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_accounts (
            staff_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('super_admin', 'library_admin', 'library_staff')),
            is_active INTEGER DEFAULT 1,
            profile_image TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            staff_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            target TEXT,
            ip_address TEXT,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (staff_id) REFERENCES staff_accounts(staff_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS programs (
            program_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            program_name TEXT NOT NULL UNIQUE,
            department_name TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS recognition_log (
            log_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id INTEGER,
            confidence REAL,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            primary_confidence REAL,
            secondary_confidence REAL,
            primary_distance REAL,
            secondary_distance REAL,
            face_quality REAL,
            method TEXT DEFAULT 'two-factor',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS imported_logs (
            import_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sr_code TEXT NOT NULL,
            name TEXT,
            gender TEXT,
            program TEXT,
            year_level TEXT,
            timestamp TIMESTAMPTZ NOT NULL,
            imported_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            import_batch TEXT
        )
        """
    )

    for table_name, column_name in (
        ("recognition_events", "captured_at"),
        ("recognition_events", "ingested_at"),
        ("imported_logs", "timestamp"),
        ("imported_logs", "imported_at"),
        ("recognition_log", "timestamp"),
        ("audit_log", "timestamp"),
        ("users", "created_at"),
        ("users", "last_updated"),
        ("users", "archived_at"),
        ("staff_accounts", "created_at"),
        ("staff_accounts", "last_login"),
        ("programs", "created_at"),
        ("programs", "last_updated"),
    ):
        _convert_column_to_timestamptz(bind, table_name, column_name)

    op.execute("ALTER TABLE recognition_events ALTER COLUMN user_id TYPE INTEGER USING user_id::integer")
    op.execute("ALTER TABLE user_embeddings ALTER COLUMN user_id TYPE INTEGER USING user_id::integer")

    if not _constraint_exists(bind, "recognition_events", "fk_recognition_events_user_id"):
        op.execute(
            """
            ALTER TABLE recognition_events
            ADD CONSTRAINT fk_recognition_events_user_id
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
            """
        )

    if not _constraint_exists(bind, "user_embeddings", "fk_user_embeddings_user_id"):
        op.execute(
            """
            ALTER TABLE user_embeddings
            ADD CONSTRAINT fk_user_embeddings_user_id
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            """
        )

    if not _constraint_exists(bind, "recognition_events", "ck_recognition_events_decision"):
        op.execute(
            """
            ALTER TABLE recognition_events
            ADD CONSTRAINT ck_recognition_events_decision
            CHECK (decision IN ('allowed', 'denied', 'unknown'))
            """
        )

    for table_name, constraint_name, column_name in (
        ("recognition_events", "ck_recognition_events_confidence", "confidence"),
        ("recognition_events", "ck_recognition_events_primary_confidence", "primary_confidence"),
        ("recognition_events", "ck_recognition_events_secondary_confidence", "secondary_confidence"),
        ("recognition_events", "ck_recognition_events_face_quality", "face_quality"),
        ("recognition_log", "ck_recognition_log_confidence", "confidence"),
        ("recognition_log", "ck_recognition_log_primary_confidence", "primary_confidence"),
        ("recognition_log", "ck_recognition_log_secondary_confidence", "secondary_confidence"),
        ("recognition_log", "ck_recognition_log_face_quality", "face_quality"),
    ):
        if not _constraint_exists(bind, table_name, constraint_name):
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ADD CONSTRAINT {constraint_name}
                CHECK ({column_name} IS NULL OR ({column_name} >= 0.0 AND {column_name} <= 1.0))
                """
            )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_ingested_at_desc
        ON recognition_events(ingested_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_sr_code
        ON recognition_events(sr_code)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_log_user_id
        ON recognition_log(user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_log_timestamp
        ON recognition_log(timestamp)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp_audit_id
        ON audit_log(timestamp DESC, audit_id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_import_batch
        ON imported_logs(import_batch)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_srcode
        ON imported_logs(sr_code)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_imported_logs_timestamp
        ON imported_logs(timestamp)
        """
    )

    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('profiles_version', '1')
        ON CONFLICT(key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('settings_version', '1')
        ON CONFLICT(key) DO NOTHING
        """
    )

    _backfill_user_embeddings(bind)


def downgrade() -> None:
    bind = op.get_bind()

    for index_name in (
        "idx_imported_logs_import_batch",
        "idx_imported_logs_timestamp",
        "idx_imported_logs_srcode",
        "idx_audit_log_timestamp_audit_id",
        "idx_recognition_log_timestamp",
        "idx_recognition_log_user_id",
        "idx_recognition_events_sr_code",
        "idx_recognition_events_ingested_at_desc",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    for table_name, constraint_name in (
        ("recognition_log", "ck_recognition_log_face_quality"),
        ("recognition_log", "ck_recognition_log_secondary_confidence"),
        ("recognition_log", "ck_recognition_log_primary_confidence"),
        ("recognition_log", "ck_recognition_log_confidence"),
        ("recognition_events", "ck_recognition_events_face_quality"),
        ("recognition_events", "ck_recognition_events_secondary_confidence"),
        ("recognition_events", "ck_recognition_events_primary_confidence"),
        ("recognition_events", "ck_recognition_events_confidence"),
        ("recognition_events", "ck_recognition_events_decision"),
        ("user_embeddings", "fk_user_embeddings_user_id"),
        ("recognition_events", "fk_recognition_events_user_id"),
    ):
        if _constraint_exists(bind, table_name, constraint_name):
            op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name}")

    if _column_type(bind, "recognition_events", "user_id") == "integer":
        op.execute("ALTER TABLE recognition_events ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint")
    if _column_type(bind, "user_embeddings", "user_id") == "integer":
        op.execute("ALTER TABLE user_embeddings ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint")

    for table_name, column_name in (
        ("recognition_events", "captured_at"),
        ("recognition_events", "ingested_at"),
        ("imported_logs", "timestamp"),
        ("imported_logs", "imported_at"),
        ("recognition_log", "timestamp"),
        ("audit_log", "timestamp"),
        ("users", "created_at"),
        ("users", "last_updated"),
        ("users", "archived_at"),
        ("staff_accounts", "created_at"),
        ("staff_accounts", "last_login"),
        ("programs", "created_at"),
        ("programs", "last_updated"),
    ):
        if _column_type(bind, table_name, column_name) == "timestamp with time zone":
            op.execute(
                f"""
                ALTER TABLE {table_name}
                ALTER COLUMN {column_name} TYPE TIMESTAMP
                USING ({column_name} AT TIME ZONE 'UTC')
                """
            )
