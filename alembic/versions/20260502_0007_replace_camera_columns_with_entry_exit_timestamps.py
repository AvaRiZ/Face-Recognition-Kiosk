"""Replace camera/station columns with entry/exit timestamps on recognition_events.

Revision ID: 20260502_0007
Revises: 20260502_0006
Create Date: 2026-05-02 00:00:02.000000

"""
from __future__ import annotations

from alembic import op


revision = "20260502_0007"
down_revision = "20260502_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS entered_at TIMESTAMP"
    )
    op.execute(
        "ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS exited_at TIMESTAMP"
    )
    op.execute(
        "UPDATE recognition_events SET entered_at = captured_at WHERE entered_at IS NULL AND camera_id = 1"
    )
    op.execute(
        "UPDATE recognition_events SET exited_at = captured_at WHERE exited_at IS NULL AND camera_id = 2"
    )
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_camera_id")
    op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS camera_id")
    op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS station_id")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_recognition_events_entered_at ON recognition_events(entered_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_recognition_events_exited_at ON recognition_events(exited_at)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS station_id TEXT")
    op.execute("ALTER TABLE recognition_events ADD COLUMN IF NOT EXISTS camera_id INTEGER NOT NULL DEFAULT 1")
    op.execute(
        "UPDATE recognition_events SET camera_id = 1 WHERE entered_at IS NOT NULL AND exited_at IS NULL"
    )
    op.execute(
        "UPDATE recognition_events SET camera_id = 2 WHERE exited_at IS NOT NULL AND entered_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_entered_at")
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_exited_at")