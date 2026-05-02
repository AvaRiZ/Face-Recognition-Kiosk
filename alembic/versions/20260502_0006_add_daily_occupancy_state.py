"""Add daily occupancy state table for realtime occupancy tracking.

Revision ID: 20260502_0006
Revises: 20260502_0005
Create Date: 2026-05-02 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = '20260502_0006'
down_revision = '20260502_0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'daily_occupancy_state',
        sa.Column('state_date', sa.Date(), nullable=False),
        sa.Column('daily_entries', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('daily_exits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('state_date')
    )


def downgrade() -> None:
    op.drop_table('daily_occupancy_state')
