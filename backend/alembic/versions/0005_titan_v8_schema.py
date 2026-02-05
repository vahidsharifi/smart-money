"""titan v8 schema updates

Revision ID: 0005
Revises: 0004
Create Date: 2024-01-04 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watch_pairs",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("pair_address", sa.String(length=128), nullable=False),
        sa.Column("dex", sa.String(length=64), nullable=True),
        sa.Column("token0_symbol", sa.String(length=32), nullable=True),
        sa.Column("token0_address", sa.String(length=128), nullable=True),
        sa.Column("token1_symbol", sa.String(length=32), nullable=True),
        sa.Column("token1_address", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("chain", "pair_address"),
    )
    op.create_index("ix_watch_pairs_chain_expires_at", "watch_pairs", ["chain", "expires_at"], unique=False)
    op.create_index("ix_watch_pairs_chain_priority", "watch_pairs", ["chain", "priority"], unique=False)
    op.create_index("ix_watch_pairs_last_seen", "watch_pairs", ["last_seen"], unique=False)

    op.add_column(
        "wallets",
        sa.Column("source", sa.String(length=32), server_default=sa.text("'autopilot'"), nullable=False),
    )
    op.add_column(
        "wallets",
        sa.Column("prior_weight", sa.Numeric(), server_default=sa.text("0.0"), nullable=False),
    )
    op.add_column(
        "wallets",
        sa.Column("merit_score", sa.Numeric(), server_default=sa.text("0.0"), nullable=False),
    )
    op.add_column("wallets", sa.Column("tier", sa.String(length=32), nullable=True))
    op.add_column(
        "wallets",
        sa.Column("tier_reason", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("wallets", sa.Column("ignore_reason", sa.Text(), nullable=True))
    op.create_index("ix_wallets_tier", "wallets", ["tier"], unique=False)
    op.create_index("ix_wallets_merit_score", "wallets", ["merit_score"], unique=False)

    op.create_table(
        "signal_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("was_sellable_entire_window", sa.Boolean(), nullable=True),
        sa.Column("min_exit_slippage_1k", sa.Numeric(), nullable=True),
        sa.Column("max_exit_slippage_1k", sa.Numeric(), nullable=True),
        sa.Column(
            "tradeable_peak_gain",
            sa.Numeric(),
            nullable=True,
            comment="Decimal fraction (1.0 = 100%).",
        ),
        sa.Column("tradeable_drawdown", sa.Numeric(), nullable=True),
        sa.Column("net_tradeable_return_est", sa.Numeric(), nullable=True),
        sa.Column("trap_flag", sa.Boolean(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "horizon_minutes", name="uq_signal_outcomes_alert_horizon"),
    )


def downgrade() -> None:
    op.drop_table("signal_outcomes")
    op.drop_index("ix_wallets_merit_score", table_name="wallets")
    op.drop_index("ix_wallets_tier", table_name="wallets")
    op.drop_column("wallets", "ignore_reason")
    op.drop_column("wallets", "tier_reason")
    op.drop_column("wallets", "tier")
    op.drop_column("wallets", "merit_score")
    op.drop_column("wallets", "prior_weight")
    op.drop_column("wallets", "source")
    op.drop_index("ix_watch_pairs_last_seen", table_name="watch_pairs")
    op.drop_index("ix_watch_pairs_chain_priority", table_name="watch_pairs")
    op.drop_index("ix_watch_pairs_chain_expires_at", table_name="watch_pairs")
    op.drop_table("watch_pairs")
