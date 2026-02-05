"""netev v2 gas tables

Revision ID: 0011
Revises: 0010
Create Date: 2024-01-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gas_cost_observations",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("tx_hash", sa.String(length=128), nullable=False),
        sa.Column("gas_cost_usd", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain", "tx_hash"),
    )
    op.create_index(
        "ix_gas_cost_observations_observed_at",
        "gas_cost_observations",
        ["observed_at"],
        unique=False,
    )

    op.create_table(
        "chain_gas_estimates",
        sa.Column("chain", sa.String(length=32), nullable=False),
        sa.Column("avg_gas_usd_1h", sa.Float(), nullable=True),
        sa.Column("p95_gas_usd_1h", sa.Float(), nullable=True),
        sa.Column("samples_1h", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chain"),
    )


def downgrade() -> None:
    op.drop_table("chain_gas_estimates")
    op.drop_index("ix_gas_cost_observations_observed_at", table_name="gas_cost_observations")
    op.drop_table("gas_cost_observations")
