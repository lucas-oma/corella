"""copilot_insights: persisted live-copilot suggestion/blockers/coach_score

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-07

Each live copilot cycle's result (app/services/copilot/live.py:run_cycle)
used to only ever reach the frontend over the WebSocket and vanish once the
connection closed. Persisted here, anchored to the transcript's own clock
(at_ms) so MeetingDetail can show them next to the transcript, and chart
coach_score over time, after the call ends.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "copilot_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at_ms", sa.Integer(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("blockers", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("coach_score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_copilot_insights_meeting_id", "copilot_insights", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_copilot_insights_meeting_id", table_name="copilot_insights")
    op.drop_table("copilot_insights")
