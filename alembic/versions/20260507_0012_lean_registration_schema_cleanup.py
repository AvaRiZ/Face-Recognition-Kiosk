"""lean registration schema cleanup

Revision ID: 20260507_0012
Revises: 20260504_0011
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260507_0012"
down_revision = "20260504_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_registrations")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_flow_type")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS flow_type")


def downgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS flow_type TEXT")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_flow_type")
    op.execute(
        """
        ALTER TABLE users
        ALTER COLUMN flow_type SET DEFAULT 'auto_entry'
        """
    )
    op.execute(
        """
        UPDATE users
        SET flow_type = 'auto_entry'
        WHERE flow_type IS NULL OR TRIM(flow_type) = ''
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ADD CONSTRAINT ck_users_flow_type
        CHECK (flow_type IN ('auto_entry', 'manual_entry', 'manual_registration'))
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_registrations (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER,
            event_id TEXT,
            registration_type TEXT NOT NULL,
            flow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            performed_by TEXT,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_user_registrations_user_id
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
            CONSTRAINT ck_user_registrations_type
                CHECK (registration_type IN ('enrolled', 'unrecognized', 'visitor')),
            CONSTRAINT ck_user_registrations_flow
                CHECK (flow_type IN ('auto_entry', 'manual_entry', 'manual_registration')),
            CONSTRAINT ck_user_registrations_status
                CHECK (status IN ('pending', 'approved', 'denied', 'canceled'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_registrations_created_at_desc
        ON user_registrations(created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_registrations_event_id
        ON user_registrations(event_id)
        """
    )
