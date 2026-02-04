"""create core tables

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("address", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain", "address"),
    )
    op.create_table(
        "tokens",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("address", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain", "address"),
    )
    op.create_table(
        "trades",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("tx_hash", sa.String(length=128), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("block_time", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain", "tx_hash", "log_index"),
    )
    op.create_index("ix_trades_block_time", "trades", ["block_time"], unique=False)
    op.create_index("ix_trades_wallet_address", "trades", ["wallet_address"], unique=False)
    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("average_price", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "wallet_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("total_value", sa.Float(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "token_risk",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("address", sa.String(length=128), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain", "address"),
    )
    op.create_index("ix_token_risk_updated_at", "token_risk", ["updated_at"], unique=False)
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("wallet_address", sa.String(length=128), nullable=False),
        sa.Column("token_address", sa.String(length=128), nullable=True),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_token_risk_updated_at", table_name="token_risk")
    op.drop_table("token_risk")
    op.drop_table("wallet_metrics")
    op.drop_table("positions")
    op.drop_index("ix_trades_wallet_address", table_name="trades")
    op.drop_index("ix_trades_block_time", table_name="trades")
    op.drop_table("trades")
    op.drop_table("tokens")
    op.drop_table("wallets")
