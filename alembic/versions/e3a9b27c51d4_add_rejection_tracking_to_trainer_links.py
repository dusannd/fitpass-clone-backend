"""Add rejection tracking to trainer_client_links

Revision ID: e3a9b27c51d4
Revises: c7d4e91af203
Create Date: 2026-09-28 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a9b27c51d4'
down_revision: Union[str, Sequence[str], None] = 'c7d4e91af203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default so every existing link starts at zero rejections instead of
    # the NOT NULL column refusing the rows that are already there.
    op.add_column(
        'trainer_client_links',
        sa.Column('rejection_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'trainer_client_links',
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('trainer_client_links', 'responded_at')
    op.drop_column('trainer_client_links', 'rejection_count')
