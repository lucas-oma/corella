"""coach score

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("coach_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "coach_score")
