"""watch_pairs v2 score and reason

Revision ID: 0009
Revises: 0008
Create Date: 2024-01-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watch_pairs",
        sa.Column("score", sa.Numeric(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("watch_pairs", sa.Column("reason", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index("ix_watch_pairs_chain_score", "watch_pairs", ["chain", "score"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_watch_pairs_chain_score", table_name="watch_pairs")
    op.drop_column("watch_pairs", "reason")
    op.drop_column("watch_pairs", "score")
