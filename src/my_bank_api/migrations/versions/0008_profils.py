"""profils

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-16 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Comptes, rubriques, classes et tiers appartiennent à un profil (décision 028). Les données existantes
# vont toutes au profil créé ici (id 1), que l'utilisateur renomme ensuite.
DEFAULT_PROFILE_ID = 1
DEFAULT_PROFILE_NAME = 'Default'


def _profile_column(batch_op, table: str) -> None:
    batch_op.add_column(sa.Column('profile_id', sa.Integer(), nullable=False, server_default=str(DEFAULT_PROFILE_ID)))
    batch_op.create_foreign_key(f'fk_{table}_profile_id_profiles', 'profiles', ['profile_id'], ['id'], ondelete='RESTRICT')
    batch_op.create_index(f'ix_{table}_profile_id', ['profile_id'], unique=False)


def upgrade() -> None:
    op.create_table(
        'profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_profiles')),
        sa.UniqueConstraint('name', name=op.f('uq_profiles_name')),
    )
    op.execute(f"INSERT INTO profiles (id, name, position) VALUES ({DEFAULT_PROFILE_ID}, '{DEFAULT_PROFILE_NAME}', 0)")

    # Index partiel : batch_alter_table ne le recopierait pas avec sa clause WHERE. Retiré puis recréé par profil.
    op.drop_index('uq_accounts_is_default', table_name='accounts')
    # Noms uniques par profil, et non plus globalement : SQLite ne sait retirer une contrainte UNIQUE
    # qu'en recréant la table (clés étrangères coupées pendant les migrations, voir migrate.py).
    with op.batch_alter_table('accounts', recreate='always') as batch_op:
        _profile_column(batch_op, 'accounts')
        batch_op.drop_constraint('uq_accounts_name', type_='unique')
        batch_op.create_unique_constraint('uq_accounts_profile_id', ['profile_id', 'name'])
    op.create_index(
        'uq_accounts_is_default', 'accounts', ['profile_id', 'is_default'], unique=True, sqlite_where=sa.text('is_default')
    )
    with op.batch_alter_table('payees', recreate='always') as batch_op:
        _profile_column(batch_op, 'payees')
        batch_op.drop_constraint('uq_payees_name', type_='unique')
        batch_op.create_unique_constraint('uq_payees_profile_id', ['profile_id', 'name'])
    for table in ('categories', 'classes'):
        with op.batch_alter_table(table, recreate='always') as batch_op:
            _profile_column(batch_op, table)


def downgrade() -> None:
    op.drop_index('uq_accounts_is_default', table_name='accounts')
    for table in ('categories', 'classes'):
        with op.batch_alter_table(table, recreate='always') as batch_op:
            batch_op.drop_index(f'ix_{table}_profile_id')
            batch_op.drop_constraint(f'fk_{table}_profile_id_profiles', type_='foreignkey')
            batch_op.drop_column('profile_id')
    for table in ('accounts', 'payees'):
        with op.batch_alter_table(table, recreate='always') as batch_op:
            batch_op.drop_constraint(f'uq_{table}_profile_id', type_='unique')
            batch_op.drop_index(f'ix_{table}_profile_id')
            batch_op.drop_constraint(f'fk_{table}_profile_id_profiles', type_='foreignkey')
            batch_op.drop_column('profile_id')
            batch_op.create_unique_constraint(f'uq_{table}_name', ['name'])
    op.create_index('uq_accounts_is_default', 'accounts', ['is_default'], unique=True, sqlite_where=sa.text('is_default'))
    op.drop_table('profiles')
