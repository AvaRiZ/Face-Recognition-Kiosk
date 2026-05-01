"""standardize timestamps and fix fk delete policy

Revision ID: 20260501_0003
Revises: 20260426_0002
Create Date: 2026-05-01

Changes:
1. Convert user_embeddings.created_at from TIMESTAMP to TIMESTAMPTZ for timezone consistency
2. Change recognition_log.user_id FK from ON DELETE NO ACTION to ON DELETE SET NULL
   to allow user archival/deletion without blocking log retention
3. Clarifies event model: recognition_events is canonical, recognition_log is legacy compat layer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260501_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Standardize user_embeddings.created_at to TIMESTAMPTZ
    op.execute(
        """
        ALTER TABLE user_embeddings
        ALTER COLUMN created_at TYPE TIMESTAMPTZ
        USING (created_at AT TIME ZONE 'UTC')
        """
    )

    # 2. Drop and recreate recognition_log FK with SET NULL delete policy
    # First, drop the existing FK constraint
    op.execute(
        """
        ALTER TABLE recognition_log
        DROP CONSTRAINT IF EXISTS recognition_log_user_id_fkey
        """
    )

    # Then, create the new FK with SET NULL delete policy
    op.execute(
        """
        ALTER TABLE recognition_log
        ADD CONSTRAINT recognition_log_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE SET NULL
        ON UPDATE NO ACTION
        """
    )

    # 3. Insert documentation comments into app_settings (for clarity)
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('event_model_canonical', 'recognition_events')
        ON CONFLICT(key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('event_model_legacy', 'recognition_log_compatibility_layer_only')
        ON CONFLICT(key) DO NOTHING
        """
    )


def downgrade() -> None:
    # 1. Revert user_embeddings.created_at to TIMESTAMP
    op.execute(
        """
        ALTER TABLE user_embeddings
        ALTER COLUMN created_at TYPE TIMESTAMP
        USING (created_at AT TIME ZONE 'UTC')
        """
    )

    # 2. Revert recognition_log FK to NO ACTION delete policy
    op.execute(
        """
        ALTER TABLE recognition_log
        DROP CONSTRAINT IF EXISTS recognition_log_user_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE recognition_log
        ADD CONSTRAINT recognition_log_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE NO ACTION
        ON UPDATE NO ACTION
        """
    )

    # 3. Remove documentation entries
    op.execute(
        """
        DELETE FROM app_settings
        WHERE key IN ('event_model_canonical', 'event_model_legacy')
        """
    )
