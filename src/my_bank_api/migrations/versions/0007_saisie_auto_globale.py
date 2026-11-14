"""saisie automatique globale, ancrée sur le mois

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD COLUMN simple (voir 0005) : colonne booléenne, défaut false pour les planifications existantes.
    op.add_column('schedules', sa.Column('use_global_lead_days', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'app_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('auto_entry_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('auto_entry_days_before_month_start', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_app_settings')),
    )
    # Ligne unique (id = 1) : l'application la lit et l'écrit sans jamais avoir à la créer.
    op.execute("INSERT INTO app_settings (id, auto_entry_enabled, auto_entry_days_before_month_start) VALUES (1, 0, 0)")


def downgrade() -> None:
    op.drop_table('app_settings')
    op.drop_column('schedules', 'use_global_lead_days')
