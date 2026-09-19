"""hook_logs table + call_types.pre_call_async / post_call_async

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19

Admins can inspect redacted pre/post hook request/response on a meeting.
Async flags (default false / sync) let a hook run in the worker instead
of blocking meeting create or auto-report completion.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "call_types",
        sa.Column("pre_call_async", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "call_types",
        sa.Column("post_call_async", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "hook_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(8), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("request_headers", sa.Text(), nullable=True),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("ran_async", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_hook_logs_meeting_id", "hook_logs", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_hook_logs_meeting_id", table_name="hook_logs")
    op.drop_table("hook_logs")
    op.drop_column("call_types", "post_call_async")
    op.drop_column("call_types", "pre_call_async")
