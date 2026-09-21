"""action_items.source — live capture vs report digest

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-21

Live copilot cycles keep writing source=live. The post-call report
replaces source=report with a short deduped list; that digest is what
the meeting page and the post-call hook show.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    source = sa.Enum("live", "report", name="action_item_source")
    source.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "action_items",
        sa.Column("source", source, nullable=False, server_default="live"),
    )


def downgrade() -> None:
    op.drop_column("action_items", "source")
    sa.Enum(name="action_item_source").drop(op.get_bind(), checkfirst=True)
