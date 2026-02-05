"""add token_risk columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("token_risk", sa.Column("token_address", sa.String(length=128), nullable=True))
    op.add_column("token_risk", sa.Column("tss", sa.Float(), nullable=True))
    op.add_column("token_risk", sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.execute(
        """
        UPDATE token_risk
        SET token_address = address,
            tss = COALESCE((components->'tss'->>'score')::double precision, score),
            flags = components->'flags'
        """
    )


def downgrade() -> None:
    op.drop_column("token_risk", "flags")
    op.drop_column("token_risk", "tss")
    op.drop_column("token_risk", "token_address")
