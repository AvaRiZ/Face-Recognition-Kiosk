"""add camera routing to recognition events

Revision ID: 20260502_0004
Revises: 20260501_0003
Create Date: 2026-05-02
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260502_0004"
down_revision = "20260501_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE recognition_events
        ADD COLUMN IF NOT EXISTS camera_id INTEGER NOT NULL DEFAULT 1
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recognition_events_camera_id
        ON recognition_events(camera_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_recognition_events_camera_id")
    op.execute("ALTER TABLE recognition_events DROP COLUMN IF EXISTS camera_id")
