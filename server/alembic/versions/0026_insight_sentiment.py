"""copilot_insights.sentiment — live-cycle tone, closed enum in app code

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-21

Nullable so existing insight rows stay valid. New writes are validated
against the closed list in app/services/copilot/sentiment.py — not a
Postgres enum, because meetings.sentiment still holds older free-text.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("copilot_insights", sa.Column("sentiment", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("copilot_insights", "sentiment")
