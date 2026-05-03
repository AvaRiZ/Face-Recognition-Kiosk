"""refactor recognition_events direction to event_type + captured_at

Revision ID: 20260503_0010
Revises: 20260503_0009
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260503_0010"
down_revision = "20260503_0009"
branch_labels = None
depends_on = None


def _column_exists(bind: sa.Connection, table_name: str, column_name: str) -> bool:
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).fetchone()
    return row is not None


def _constraint_exists(bind: sa.Connection, table_name: str, constraint_name: str) -> bool:
    row = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = :table_name
              AND c.conname = :constraint_name
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "recognition_events", "event_type"):
        op.execute("ALTER TABLE recognition_events ADD COLUMN event_type TEXT")

    has_entered_at = _column_exists(bind, "recognition_events", "entered_at")
    has_exited_at = _column_exists(bind, "recognition_events", "exited_at")
    if has_entered_at and has_exited_at:
        op.execute(
            """
            UPDATE recognition_events
            SET event_type = CASE
                WHEN entered_at IS NOT NULL AND exited_at IS NULL THEN 'entry'
                WHEN exited_at IS NOT NULL AND entered_at IS NULL THEN 'exit'
                WHEN entered_at IS NOT NULL THEN 'entry'
                WHEN exited_at IS NOT NULL THEN 'exit'
                ELSE event_type
            END
            WHERE event_type IS NULL OR TRIM(event_type) = ''
            """
        )

    op.execute(
        """
        UPDATE recognition_events
        SET event_type = CASE
            WHEN LOWER(TRIM(COALESCE(event_type, ''))) IN ('exit', '2') THEN 'exit'
            ELSE 'entry'
        END
        """
    )
    op.execute(
        """
        ALTER TABLE recognition_events
        ALTER COLUMN event_type SET DEFAULT 'entry'
        """
    )
    op.execute(
        """
        ALTER TABLE recognition_events
        ALTER COLUMN event_type SET NOT NULL
        """
    )
    if not _constraint_exists(bind, "recognition_events", "ck_recognition_events_event_type"):
        op.execute(
            """
            ALTER TABLE recognition_events
            ADD CONSTRAINT ck_recognition_events_event_type
            CHECK (event_type IN ('entry', 'exit'))
            """
        )

    op.execute("DROP INDEX IF EXISTS idx_recognition_events_entered_at")
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_exited_at")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_event_type
        ON recognition_events(event_type)
        """
    )

    if has_entered_at:
        op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS entered_at")
    if has_exited_at:
        op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS exited_at")


def downgrade() -> None:
    op.execute("ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS entered_at TIMESTAMP")
    op.execute("ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS exited_at TIMESTAMP")
    op.execute(
        """
        UPDATE recognition_events
        SET entered_at = captured_at
        WHERE event_type = 'entry' AND entered_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE recognition_events
        SET exited_at = captured_at
        WHERE event_type = 'exit' AND exited_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_entered_at
        ON recognition_events(entered_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_exited_at
        ON recognition_events(exited_at)
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_event_type")
    op.execute("ALTER TABLE recognition_events DROP CONSTRAINT IF EXISTS ck_recognition_events_event_type")
    op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS event_type")
