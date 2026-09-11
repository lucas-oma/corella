"""api_keys.max_duration_minutes — per-key cap on a live API recording

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-10

A live WebSocket authenticated with an API key (app/ws/live_session.py)
runs until the client stops or disconnects — nothing previously bounded
how long a hung/forgotten integration could stream. This column is that
bound, enforced server-side (close code 4410) and editable in Settings.
Existing keys get the same 60-minute default new keys get; there is no
unlimited option (the allowed range is 1..480 minutes).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "max_duration_minutes",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "max_duration_minutes")
