"""llm usage events

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "provider",
            sa.Enum("anthropic", "openai", "gemini", "ollama", name="usage_event_provider"),
            nullable=False,
        ),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("live_cycle", "report", name="usage_event_kind"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_llm_usage_events_meeting_id", "llm_usage_events", ["meeting_id"])
    op.create_index("ix_llm_usage_events_owner_id", "llm_usage_events", ["owner_id"])
    op.create_index("ix_llm_usage_events_created_at", "llm_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_usage_events")
    sa.Enum(name="usage_event_kind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="usage_event_provider").drop(op.get_bind(), checkfirst=True)
