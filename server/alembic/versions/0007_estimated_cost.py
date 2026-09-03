"""estimated cost

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("estimated_cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "estimated_cost_usd")
