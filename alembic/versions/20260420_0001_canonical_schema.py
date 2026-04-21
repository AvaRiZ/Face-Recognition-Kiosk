"""create canonical thesis schema

Revision ID: 20260420_0001
Revises:
Create Date: 2026-04-20
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260420_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS recognition_events (
            id BIGSERIAL PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            station_id TEXT,
            user_id BIGINT,
            sr_code TEXT,
            decision TEXT NOT NULL,
            confidence DOUBLE PRECISION,
            primary_confidence DOUBLE PRECISION,
            secondary_confidence DOUBLE PRECISION,
            primary_distance DOUBLE PRECISION,
            secondary_distance DOUBLE PRECISION,
            face_quality DOUBLE PRECISION,
            method TEXT,
            captured_at TIMESTAMP,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            payload_json TEXT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_captured_at
        ON recognition_events(captured_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_user_id
        ON recognition_events(user_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_embeddings (
            embedding_id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            model_name TEXT NOT NULL,
            embedding BYTEA NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_embeddings_user_model
        ON user_embeddings(user_id, model_name)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_embeddings")
    op.execute("DROP TABLE IF EXISTS recognition_events")
