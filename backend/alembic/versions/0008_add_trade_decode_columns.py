"""add dex decode columns to trades

Revision ID: 0008
Revises: 0007
Create Date: 2024-01-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("dex", sa.String(length=64), nullable=True))
    op.add_column("trades", sa.Column("pair_address", sa.String(length=128), nullable=True))
    op.add_column(
        "trades",
        sa.Column("decode_confidence", sa.Float(), server_default=sa.text("0.0"), nullable=False),
    )
    op.create_check_constraint(
        "ck_trades_pair_address_when_decoded",
        "trades",
        "decode_confidence < 0.6 OR pair_address IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_trades_pair_address_when_decoded", "trades", type_="check")
    op.drop_column("trades", "decode_confidence")
    op.drop_column("trades", "pair_address")
    op.drop_column("trades", "dex")
