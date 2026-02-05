"""add alerts tss/conviction columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("tss", sa.Float(), nullable=True))
    op.add_column("alerts", sa.Column("conviction", sa.Float(), nullable=True))

    op.execute(
        """
        UPDATE alerts
        SET tss = COALESCE((reasons->>'tss')::double precision, tss),
            conviction = COALESCE((reasons->>'conviction')::double precision, conviction)
        """
    )


def downgrade() -> None:
    op.drop_column("alerts", "conviction")
    op.drop_column("alerts", "tss")
