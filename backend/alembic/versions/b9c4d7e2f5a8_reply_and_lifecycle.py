"""reply drafts and lifecycle fields on tasks (stage 6)

Revision ID: b9c4d7e2f5a8
Revises: a8b3c6d1e4f7
Create Date: 2026-10-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c4d7e2f5a8'
down_revision: Union[str, Sequence[str], None] = 'a8b3c6d1e4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tasks') as batch:
        batch.add_column(sa.Column('reply_sent_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('wait_from', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('no_reply_asked_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('change_log', sa.JSON(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('changed_unseen', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('related', sa.JSON(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('merge_analysis', sa.JSON(), nullable=False, server_default='{}'))
    op.create_table(
        'reply_drafts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('purpose', sa.String(length=10), nullable=False),
        sa.Column('to_addr', sa.String(length=255), nullable=False),
        sa.Column('cc_candidates', sa.JSON(), nullable=False),
        sa.Column('cc_selected', sa.JSON(), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('warnings', sa.JSON(), nullable=False),
        sa.Column('user_input', sa.Text(), nullable=False),
        sa.Column('gmail_message_id', sa.String(length=64), nullable=True),
        sa.Column('sending_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', name='uq_reply_drafts_task_id'),
    )
    op.create_index('ix_reply_drafts_user_id', 'reply_drafts', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_reply_drafts_user_id', table_name='reply_drafts')
    op.drop_table('reply_drafts')
    with op.batch_alter_table('tasks') as batch:
        for col in ('merge_analysis', 'related', 'changed_unseen', 'change_log',
                    'no_reply_asked_at', 'wait_from', 'reply_sent_at'):
            batch.drop_column(col)
