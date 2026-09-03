"""kb_document chunk_count + error

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kb_documents", sa.Column("chunk_count", sa.Integer(), nullable=True))
    op.add_column("kb_documents", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("kb_documents", "error")
    op.drop_column("kb_documents", "chunk_count")
