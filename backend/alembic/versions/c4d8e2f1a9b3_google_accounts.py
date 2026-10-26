"""google_accounts table and Gmail columns on emails

Revision ID: c4d8e2f1a9b3
Revises: b7c2d1e4f5a6
Create Date: 2026-10-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8e2f1a9b3'
down_revision: Union[str, Sequence[str], None] = 'b7c2d1e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'google_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('google_email', sa.String(length=255), nullable=False),
        sa.Column('refresh_token_enc', sa.Text(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('history_id', sa.String(length=40), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('sync_lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    with op.batch_alter_table('emails') as batch:
        batch.add_column(sa.Column('gmail_message_id', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('gmail_thread_id', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('rfc_message_id', sa.String(length=500), nullable=True))
        batch.add_column(sa.Column('cc', sa.JSON(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('received_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('triage', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('triage_reason', sa.Text(), nullable=False, server_default=''))
        batch.add_column(sa.Column('attachments', sa.JSON(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('gmail_meta', sa.JSON(), nullable=False, server_default='{}'))
        batch.create_index('ix_emails_gmail_thread_id', ['gmail_thread_id'])
        batch.create_index('ix_emails_triage', ['triage'])
        batch.create_unique_constraint('uq_emails_user_gmail_id', ['user_id', 'gmail_message_id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('emails') as batch:
        batch.drop_constraint('uq_emails_user_gmail_id', type_='unique')
        batch.drop_index('ix_emails_triage')
        batch.drop_index('ix_emails_gmail_thread_id')
        for col in ('gmail_meta', 'attachments', 'triage_reason', 'triage', 'received_at',
                    'cc', 'rfc_message_id', 'gmail_thread_id', 'gmail_message_id'):
            batch.drop_column(col)
    op.drop_table('google_accounts')
