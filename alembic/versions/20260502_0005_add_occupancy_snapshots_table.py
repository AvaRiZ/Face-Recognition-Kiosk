"""Add occupancy_snapshots table for historical occupancy tracking.

Revision ID: 20260502_0005
Revises: 20260502_0004
Create Date: 2026-05-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260502_0005'
down_revision = '20260502_0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'occupancy_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('snapshot_timestamp', sa.DateTime(), nullable=False),
        sa.Column('occupancy_count', sa.Integer(), nullable=False),
        sa.Column('capacity_limit', sa.Integer(), nullable=False),
        sa.Column('capacity_warning', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('daily_entries', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('daily_exits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'idx_occupancy_snapshots_timestamp_desc',
        'occupancy_snapshots',
        ['snapshot_timestamp'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('idx_occupancy_snapshots_timestamp_desc', table_name='occupancy_snapshots')
    op.drop_table('occupancy_snapshots')
