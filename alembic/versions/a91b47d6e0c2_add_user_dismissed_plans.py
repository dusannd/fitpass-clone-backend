"""add user dismissed plans

Revision ID: a91b47d6e0c2
Revises: c3d41f92a7be
Create Date: 2026-08-10 14:22:05.117402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91b47d6e0c2'
down_revision: Union[str, Sequence[str], None] = 'c3d41f92a7be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- MEMBERS CAN HIDE AN ASSIGNED PLAN ---
    # Same two column shape as user_saved_plans. Both sides cascade, so deleting either
    # the member or the plan cleans the row up on its own.
    op.create_table(
        'user_dismissed_plans',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['workout_plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'plan_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the table un-hides every plan, which is the safe direction to fail in.
    op.drop_table('user_dismissed_plans')
