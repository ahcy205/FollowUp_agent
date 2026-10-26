"""calendar sync fields on tasks

Revision ID: e6f1a4b8c2d5
Revises: d5e9f3a2b7c4
Create Date: 2026-10-26 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f1a4b8c2d5'
down_revision: Union[str, Sequence[str], None] = 'd5e9f3a2b7c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('calendar_event_id', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('calendar_event_url', sa.Text(), nullable=False, server_default=''))
        batch.add_column(sa.Column('calendar_state', sa.String(length=20), nullable=False, server_default='none'))
        batch.add_column(sa.Column('calendar_error', sa.Text(), nullable=False, server_default=''))
        batch.add_column(sa.Column('calendar_attempts', sa.Integer(), nullable=False, server_default='0'))
        batch.create_index('ix_tasks_calendar_state', ['calendar_state'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.drop_index('ix_tasks_calendar_state')
        for col in ('calendar_attempts', 'calendar_error', 'calendar_state',
                    'calendar_event_url', 'calendar_event_id'):
            batch.drop_column(col)
