"""pre/post call-type hooks: split the single webhook into post_call_*,
add pre_call_*, and meetings.pre_call_context

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-08

The single webhook_* config CallType already had only ever fired after a
call finished — it's renamed post_call_* here (existing configured
webhooks keep working, unchanged, just under new column names) and given
a sibling pre_call_* config that fires before a call starts
(app/api/meetings.py:create_meeting), with a special "use response as
conversation context" flag (pre_call_use_as_context) that, when on,
stores the fetched response on the new meetings.pre_call_context column
for the live copilot to read alongside the knowledge base
(app/services/copilot/live.py:run_cycle). post_call_send_full_payload is
the post-call's equivalent special feature — send everything (transcript,
report, CopilotInsight timeline, action items) as one JSON payload
instead of the admin's own body template.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("call_types", "webhook_enabled", new_column_name="post_call_enabled")
    op.alter_column("call_types", "webhook_url", new_column_name="post_call_url")
    op.alter_column("call_types", "webhook_method", new_column_name="post_call_method")
    op.alter_column(
        "call_types", "webhook_headers_encrypted", new_column_name="post_call_headers_encrypted"
    )
    op.alter_column(
        "call_types", "webhook_body_template", new_column_name="post_call_body_template"
    )
    op.add_column(
        "call_types",
        sa.Column("post_call_send_full_payload", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column(
        "call_types",
        sa.Column("pre_call_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("call_types", sa.Column("pre_call_url", sa.String(2048), nullable=True))
    op.add_column(
        "call_types",
        sa.Column("pre_call_method", sa.String(16), nullable=False, server_default="GET"),
    )
    op.add_column("call_types", sa.Column("pre_call_headers_encrypted", sa.Text(), nullable=True))
    op.add_column("call_types", sa.Column("pre_call_body_template", sa.Text(), nullable=True))
    op.add_column(
        "call_types",
        sa.Column("pre_call_use_as_context", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column("meetings", sa.Column("pre_call_context", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "pre_call_context")

    op.drop_column("call_types", "pre_call_use_as_context")
    op.drop_column("call_types", "pre_call_body_template")
    op.drop_column("call_types", "pre_call_headers_encrypted")
    op.drop_column("call_types", "pre_call_method")
    op.drop_column("call_types", "pre_call_url")
    op.drop_column("call_types", "pre_call_enabled")

    op.drop_column("call_types", "post_call_send_full_payload")
    op.alter_column(
        "call_types", "post_call_body_template", new_column_name="webhook_body_template"
    )
    op.alter_column(
        "call_types", "post_call_headers_encrypted", new_column_name="webhook_headers_encrypted"
    )
    op.alter_column("call_types", "post_call_method", new_column_name="webhook_method")
    op.alter_column("call_types", "post_call_url", new_column_name="webhook_url")
    op.alter_column("call_types", "post_call_enabled", new_column_name="webhook_enabled")
