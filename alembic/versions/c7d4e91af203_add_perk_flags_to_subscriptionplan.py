"""Add perk flags to SubscriptionPlan

Revision ID: c7d4e91af203
Revises: b25abfb4dd06
Create Date: 2026-08-14 12:04:31.550218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d4e91af203'
down_revision: Union[str, Sequence[str], None] = 'b25abfb4dd06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The five perks, in the order they are advertised on the pricing card.
PERK_COLUMNS = (
    "includes_trainer",
    "includes_group_classes",
    "has_sauna_access",
    "has_towel_service",
    "allows_guest",
)


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. ADD THE COLUMNS ---
    # server_default=sa.false() is what fills the existing rows; without it these
    # ALTER TABLEs would fail on a non-empty table because the columns are NOT NULL.
    #
    # The is_active column on this same table was added nullable with no default,
    # which is why PlanResponse still carries a validator to turn NULL into True.
    # Doing it properly here means these five never need one.
    for column in PERK_COLUMNS:
        op.add_column(
            'subscription_plans',
            sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False),
        )

    # --- 2. BACKFILL FROM THE EXISTING TIER ---
    # Without this every plan in the database reads as bare the moment the migration
    # lands - including the VIP ones - so the feature would look broken until an
    # admin clicked through every plan by hand. Migration b25abfb4dd06 backfilled
    # `tier` the same way, from the plan names.

    # VIP is the top tier: it gets everything, personal training included.
    op.execute(
        """
        UPDATE subscription_plans
           SET includes_trainer = true,
               includes_group_classes = true,
               has_sauna_access = true,
               has_towel_service = true,
               allows_guest = true
         WHERE tier = 'VIP'
        """
    )

    # Pro gets the comfort perks but NOT a trainer - that is the one thing that
    # separates it from VIP, and the only flag the backend actually enforces.
    op.execute(
        """
        UPDATE subscription_plans
           SET has_sauna_access = true,
               has_towel_service = true
         WHERE tier = 'Pro'
        """
    )

    # Standard keeps every flag at the server_default of false. Nothing to do.


def downgrade() -> None:
    """Downgrade schema."""
    for column in reversed(PERK_COLUMNS):
        op.drop_column('subscription_plans', column)
