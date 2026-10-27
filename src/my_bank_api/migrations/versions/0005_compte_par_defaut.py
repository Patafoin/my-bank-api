"""compte par défaut

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pas de batch_alter_table ici : SQLite l'émule en recréant la table, ce qui viole les clés étrangères
    # des tables qui référencent accounts (transactions, schedules…) tant que foreign_keys=ON. Un simple
    # ADD COLUMN est nativement supporté par SQLite et n'a pas ce problème.
    op.add_column('accounts', sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()))
    # Index partiel : ne contraint que les lignes is_default = true, donc un seul compte par défaut à la fois.
    op.create_index('uq_accounts_is_default', 'accounts', ['is_default'], unique=True, sqlite_where=sa.text('is_default'))


def downgrade() -> None:
    op.drop_index('uq_accounts_is_default', table_name='accounts')
    op.drop_column('accounts', 'is_default')
