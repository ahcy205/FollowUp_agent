"""remove demo mode and goal-input agent: drop agent_runs · approval_requests · customers · documents

메일이 아닌 업무(목표 입력 · 후속 · 데모 가상 업무)와 그 기록 · 초안, Gmail이 아닌 메일(데모 가상 메일)을 지운다.
그 테이블을 가리키던 칸과 Agent만 쓰던 칸(tasks.agent_enabled · result_summary)도 지운다.
downgrade는 칸 · 테이블을 빈 채로 되살린다(지운 행은 돌아오지 않는다).

Revision ID: d8e2f6a1c9b4
Revises: c1d5e8f2a7b3
Create Date: 2026-10-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e2f6a1c9b4'
down_revision: Union[str, Sequence[str], None] = 'c1d5e8f2a7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NON_MAIL = "SELECT id FROM tasks WHERE origin <> 'mail'"


def upgrade() -> None:
    """Upgrade schema."""
    # 1. agent_runs를 가리키는 칸 · 테이블부터 지운다(PostgreSQL은 참조가 남아 있으면 테이블을 못 지운다).
    with op.batch_alter_table('agent_action_logs', schema=None) as batch_op:
        batch_op.drop_column('run_id')
    op.drop_table('approval_requests')  # 인덱스는 테이블과 함께 지워진다
    op.drop_table('agent_runs')

    # 2. 메일이 아닌 업무와 거기 딸린 행, Gmail이 아닌 메일을 지운다. (마이그레이션 연결은 SQLite FK를 켜지 않으므로
    #    CASCADE · SET NULL에 기대지 않고 직접 지운다.)
    op.execute(f"DELETE FROM agent_action_logs WHERE task_id IN ({_NON_MAIL})")
    op.execute(f"DELETE FROM reply_drafts WHERE task_id IN ({_NON_MAIL})")
    op.execute(f"UPDATE emails SET task_id = NULL WHERE task_id IN ({_NON_MAIL})")
    op.execute(f"UPDATE tasks SET merged_into_id = NULL WHERE merged_into_id IN ({_NON_MAIL})")
    op.execute("UPDATE tasks SET parent_task_id = NULL")
    op.execute("DELETE FROM tasks WHERE origin <> 'mail'")
    op.execute("DELETE FROM emails WHERE gmail_message_id IS NULL")

    # 3. customers · documents를 가리키던 칸과 Agent만 쓰던 칸을 지운다. 인덱스를 먼저 지운다.
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.drop_index('ix_emails_customer_id')
        batch_op.drop_column('customer_id')
        batch_op.drop_column('document_id')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index('ix_tasks_parent_task_id')
        batch_op.drop_column('customer_id')
        batch_op.drop_column('parent_task_id')
        batch_op.drop_column('agent_enabled')
        batch_op.drop_column('result_summary')
    op.drop_table('documents')
    op.drop_table('customers')


def downgrade() -> None:
    """Downgrade schema. 칸 · 테이블만 빈 채로 되살린다."""
    op.create_table('customers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('company_name', sa.String(length=200), nullable=False),
    sa.Column('contact_name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=False),
    sa.Column('metadata', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customers_user_id'), ['user_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('document_type', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('customer_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('parent_task_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('agent_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('result_summary', sa.Text(), nullable=False, server_default=''))
        batch_op.create_foreign_key('fk_tasks_customer_id_customers', 'customers', ['customer_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_foreign_key('fk_tasks_parent_task_id_tasks', 'tasks', ['parent_task_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_tasks_parent_task_id'), ['parent_task_id'], unique=False)
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.add_column(sa.Column('customer_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('document_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_emails_customer_id_customers', 'customers', ['customer_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_foreign_key('fk_emails_document_id_documents', 'documents', ['document_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_emails_customer_id'), ['customer_id'], unique=False)

    op.create_table('agent_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('messages', sa.JSON(), nullable=False),
    sa.Column('pending_results', sa.JSON(), nullable=False),
    sa.Column('steps', sa.Integer(), nullable=False),
    sa.Column('final_message', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('usage', sa.JSON(), nullable=False, server_default='{}'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_runs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_runs_task_id'), ['task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_runs_user_id'), ['user_id'], unique=False)

    op.create_table('approval_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('tool_use_id', sa.String(length=100), nullable=False),
    sa.Column('tool_name', sa.String(length=50), nullable=False),
    sa.Column('tool_input', sa.JSON(), nullable=False),
    sa.Column('email_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('user_comment', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['email_id'], ['emails.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('approval_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approval_requests_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_approval_requests_task_id'), ['task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_approval_requests_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('agent_action_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('run_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_agent_action_logs_run_id_agent_runs', 'agent_runs', ['run_id'], ['id'],
                                    ondelete='SET NULL')
