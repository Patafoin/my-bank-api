"""date limite de paiement

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD COLUMN simple, sans batch_alter_table (voir 0005) : colonnes nullables, aucune donnée existante touchée.
    op.add_column('transactions', sa.Column('due_date', sa.Date(), nullable=True))
    op.add_column('schedules', sa.Column('due_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('schedules', 'due_days')
    op.drop_column('transactions', 'due_date')
