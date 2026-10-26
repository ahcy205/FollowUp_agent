"""mail fields on tasks (thread link, brief, deadline)

Revision ID: d5e9f3a2b7c4
Revises: c4d8e2f1a9b3
Create Date: 2026-10-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e9f3a2b7c4'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f1a9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('gmail_thread_id', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('brief', sa.JSON(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('deadline_all_day', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('deadline_kind', sa.String(length=10), nullable=False, server_default='none'))
        batch.add_column(sa.Column('deadline_source', sa.Text(), nullable=False, server_default=''))
        batch.create_index('ix_tasks_gmail_thread_id', ['gmail_thread_id'])
        batch.create_unique_constraint('uq_tasks_user_gmail_thread', ['user_id', 'gmail_thread_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.drop_constraint('uq_tasks_user_gmail_thread', type_='unique')
        batch.drop_index('ix_tasks_gmail_thread_id')
        for col in ('deadline_source', 'deadline_kind', 'deadline_all_day', 'deadline_at',
                    'brief', 'gmail_thread_id'):
            batch.drop_column(col)
