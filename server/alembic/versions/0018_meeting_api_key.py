"""meetings.api_key_id — tracks which API key (if any) is/was actually
streaming a meeting's live session

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-10

Set the moment a live WS session's auth resolves via an API key (app/ws/
live_session.py), not at meeting creation — that's the moment that
actually answers "is this being recorded by an external system," and it
doubles as a permanent audit trail once the call ends (app/models/
meeting.py:api_key_name), surfaced in the UI as a "Live via API" /
"Recorded via API" badge.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "api_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("meetings", "api_key_id")
