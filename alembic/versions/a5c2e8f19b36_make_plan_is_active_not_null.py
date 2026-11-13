"""Backfill and enforce NOT NULL on subscription_plans.is_active

Revision ID: a5c2e8f19b36
Revises: e3a9b27c51d4
Create Date: 2026-11-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5c2e8f19b36'
down_revision: Union[str, Sequence[str], None] = 'e3a9b27c51d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. The column was first added nullable with no backfill, so older rows hold
    # NULL. NULL is not "hidden", so treat it as active before locking the column.
    op.execute("UPDATE subscription_plans SET is_active = true WHERE is_active IS NULL")

    # 2. Now the constraint can be added, with a default for future inserts.
    op.alter_column(
        'subscription_plans',
        'is_active',
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # The backfilled values stay as they are; only the constraint and default go.
    op.alter_column(
        'subscription_plans',
        'is_active',
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )
