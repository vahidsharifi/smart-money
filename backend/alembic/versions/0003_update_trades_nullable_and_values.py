"""update trades nullable fields and add block/usd columns

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("block_number", sa.Integer(), nullable=True))
    op.add_column("trades", sa.Column("usd_value", sa.Float(), nullable=True))
    op.alter_column("trades", "wallet_address", existing_type=sa.String(length=128), nullable=True)
    op.alter_column("trades", "token_address", existing_type=sa.String(length=128), nullable=True)
    op.alter_column("trades", "block_time", existing_type=sa.DateTime(), nullable=True)


def downgrade() -> None:
    op.alter_column("trades", "block_time", existing_type=sa.DateTime(), nullable=False)
    op.alter_column("trades", "token_address", existing_type=sa.String(length=128), nullable=False)
    op.alter_column("trades", "wallet_address", existing_type=sa.String(length=128), nullable=False)
    op.drop_column("trades", "usd_value")
    op.drop_column("trades", "block_number")
