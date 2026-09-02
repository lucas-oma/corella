"""call type and expanded report fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    call_type = sa.Enum(
        "meeting", "sales", "support", "interview", "one_on_one", name="meeting_call_type"
    )
    call_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "meetings",
        sa.Column(
            "call_type", call_type, nullable=False, server_default="meeting"
        ),
    )
    op.add_column("meetings", sa.Column("key_topics", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("meetings", sa.Column("sentiment", sa.String(255), nullable=True))
    op.add_column("meetings", sa.Column("notable_quotes", postgresql.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "notable_quotes")
    op.drop_column("meetings", "sentiment")
    op.drop_column("meetings", "key_topics")
    op.drop_column("meetings", "call_type")
    sa.Enum(name="meeting_call_type").drop(op.get_bind(), checkfirst=True)
