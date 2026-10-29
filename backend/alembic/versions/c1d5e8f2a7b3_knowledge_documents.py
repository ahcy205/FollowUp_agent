"""knowledge sources and chunks, reply draft sources (RAG stage 1)

Revision ID: c1d5e8f2a7b3
Revises: b9c4d7e2f5a8
Create Date: 2026-10-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d5e8f2a7b3'
down_revision: Union[str, Sequence[str], None] = 'b9c4d7e2f5a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'knowledge_sources',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('file_type', sa.String(length=10), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('visibility', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('notes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', 'content_hash', name='uq_knowledge_sources_user_hash'),
    )
    op.create_index('ix_knowledge_sources_user_id', 'knowledge_sources', ['user_id'])
    op.create_table(
        'knowledge_chunks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('knowledge_sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('lines', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('flagged', sa.Boolean(), nullable=False),
    )
    op.create_index('ix_knowledge_chunks_source_id', 'knowledge_chunks', ['source_id'])
    op.create_index('ix_knowledge_chunks_user_id', 'knowledge_chunks', ['user_id'])
    with op.batch_alter_table('reply_drafts') as batch:
        batch.add_column(sa.Column('sources', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('reply_drafts') as batch:
        batch.drop_column('sources')
    op.drop_index('ix_knowledge_chunks_user_id', table_name='knowledge_chunks')
    op.drop_index('ix_knowledge_chunks_source_id', table_name='knowledge_chunks')
    op.drop_table('knowledge_chunks')
    op.drop_index('ix_knowledge_sources_user_id', table_name='knowledge_sources')
    op.drop_table('knowledge_sources')
