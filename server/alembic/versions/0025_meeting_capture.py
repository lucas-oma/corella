"""meetings.capture_mode / capture_app — how audio arrived

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-21

Call type is "sales vs 1:1". This is the capture topology: open mic
(default), a shared-tab meeting (Meet/Teams/Zoom), or an upload.
Transcript / hook / report labels follow this — open mic uses Speaker
N, not Me on every line.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    mode = sa.Enum("open_mic", "meeting_tab", "upload", name="meeting_capture_mode")
    mode.create(op.get_bind(), checkfirst=True)
    app = sa.Enum("meet", "teams", "zoom", "other", name="meeting_capture_app")
    app.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "meetings",
        sa.Column("capture_mode", mode, nullable=False, server_default="open_mic"),
    )
    op.add_column("meetings", sa.Column("capture_app", app, nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "capture_app")
    op.drop_column("meetings", "capture_mode")
    sa.Enum(name="meeting_capture_app").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="meeting_capture_mode").drop(op.get_bind(), checkfirst=True)
