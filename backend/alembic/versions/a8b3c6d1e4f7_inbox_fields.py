"""inbox screen fields on tasks (snooze, priority source, merge)

Revision ID: a8b3c6d1e4f7
Revises: f7a2b5c9d3e6
Create Date: 2026-10-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b3c6d1e4f7'
down_revision: Union[str, Sequence[str], None] = 'f7a2b5c9d3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('snooze_returned_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('priority_source', sa.String(length=10), nullable=False, server_default='llm'))
        batch.add_column(sa.Column('merged_into_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('merge_dismissed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('merge_undo', sa.JSON(), nullable=False, server_default='{}'))
        batch.create_foreign_key('fk_tasks_merged_into_id_tasks', 'tasks', ['merged_into_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_tasks_merged_into_id', ['merged_into_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.drop_index('ix_tasks_merged_into_id')
        batch.drop_constraint('fk_tasks_merged_into_id_tasks', type_='foreignkey')
        for col in ('merge_undo', 'merge_dismissed', 'merged_into_id',
                    'priority_source', 'snooze_returned_at', 'snoozed_until'):
            batch.drop_column(col)
