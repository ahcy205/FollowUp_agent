"""agent_runs.usage: normalized LLM token usage per run

Revision ID: b7c2d1e4f5a6
Revises: eff0a3cff922
Create Date: 2026-10-10 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c2d1e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'eff0a3cff922'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('agent_runs', sa.Column('usage', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_runs', 'usage')
