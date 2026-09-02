"""voice identities

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "linked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_voice_identities_group_id", "voice_identities", ["group_id"])
    op.create_index("ix_voice_identities_linked_user_id", "voice_identities", ["linked_user_id"])

    op.add_column(
        "speakers",
        sa.Column(
            "voice_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("voice_identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Never populated anywhere in the codebase — superseded by
    # voice_identities + speakers.voice_identity_id above (a durable
    # identity can span many Speaker rows across many meetings, which a
    # column on Speaker itself never could).
    op.drop_column("speakers", "embedding_ref")


def downgrade() -> None:
    op.add_column("speakers", sa.Column("embedding_ref", sa.String(64), nullable=True))
    op.drop_column("speakers", "voice_identity_id")
    op.drop_table("voice_identities")
