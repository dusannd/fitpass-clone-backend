"""add live workout fields and per set exercise logs

Revision ID: c3d41f92a7be
Revises: 5a7c39932c1c
Create Date: 2026-08-10 02:41:12.884301

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d41f92a7be'
down_revision: Union[str, Sequence[str], None] = '5a7c39932c1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. TRAINER SETUP FIELDS ON EXERCISES ---
    # weight_step_kg is NOT NULL, so existing rows need a server_default to fall back on.
    op.add_column('exercises', sa.Column('recommended_weight_kg', sa.Float(), nullable=True))
    op.add_column('exercises', sa.Column('weight_step_kg', sa.Float(), server_default='2.5', nullable=False))
    op.add_column('exercises', sa.Column('instructions', sa.String(), nullable=True))

    # --- 2. EXERCISE LOGS BECOME ONE ROW PER SET ---
    op.add_column('exercise_logs', sa.Column('set_number', sa.Integer(), server_default='1', nullable=False))

    # --- 3. EXPAND THE OLD AGGREGATED ROWS ---
    # An old row said "3 sets of 10 at 85.5 kg" in a single line. We turn it into
    # 3 real rows so the history screen does not suddenly show old workouts as
    # single set sessions. The first row keeps set_number 1 (the default above),
    # and we insert copies numbered 2..sets_completed.
    # sets_completed is still NOT NULL at this point (we only drop it below), so the
    # copies have to carry it along even though it is about to disappear.
    op.execute(
        """
        INSERT INTO exercise_logs (session_id, exercise_id, set_number, reps_completed, weight_kg, sets_completed)
        SELECT session_id, exercise_id, gs, reps_completed, weight_kg, sets_completed
        FROM exercise_logs, generate_series(2, sets_completed) AS gs
        WHERE sets_completed > 1
        """
    )

    op.drop_column('exercise_logs', 'sets_completed')

    # --- 4. INDEX ---
    # We now write about three times more rows, and every read groups them by
    # (session, exercise).
    op.create_index(
        'ix_exercise_logs_session_exercise',
        'exercise_logs',
        ['session_id', 'exercise_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_exercise_logs_session_exercise', table_name='exercise_logs')

    # --- 1. COLLAPSE THE PER SET ROWS BACK INTO ONE ROW PER EXERCISE ---
    op.add_column('exercise_logs', sa.Column('sets_completed', sa.Integer(), server_default='1', nullable=False))

    # Count how many sets each (session, exercise) has and write it onto set 1.
    op.execute(
        """
        UPDATE exercise_logs AS el
        SET sets_completed = totals.set_count
        FROM (
            SELECT session_id, exercise_id, COUNT(*) AS set_count
            FROM exercise_logs
            GROUP BY session_id, exercise_id
        ) AS totals
        WHERE el.session_id = totals.session_id
          AND el.exercise_id IS NOT DISTINCT FROM totals.exercise_id
          AND el.set_number = 1
        """
    )

    # Everything above set 1 was created by the upgrade, so it goes away again.
    op.execute("DELETE FROM exercise_logs WHERE set_number > 1")

    op.drop_column('exercise_logs', 'set_number')

    # --- 2. DROP THE TRAINER SETUP FIELDS ---
    op.drop_column('exercises', 'instructions')
    op.drop_column('exercises', 'weight_step_kg')
    op.drop_column('exercises', 'recommended_weight_kg')
