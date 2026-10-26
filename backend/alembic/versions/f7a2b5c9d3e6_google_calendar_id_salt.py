"""calendar id salt on google accounts

Revision ID: f7a2b5c9d3e6
Revises: e6f1a4b8c2d5
Create Date: 2026-10-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a2b5c9d3e6'
down_revision: Union[str, Sequence[str], None] = 'e6f1a4b8c2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('google_accounts') as batch:
        batch.add_column(sa.Column('calendar_id_salt', sa.String(length=16), nullable=False, server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('google_accounts') as batch:
        batch.drop_column('calendar_id_salt')
