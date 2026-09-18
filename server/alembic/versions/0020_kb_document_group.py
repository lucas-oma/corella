"""kb_documents.group_id — admin assigns a document to a group's shared KB

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-18

Knowledge-base writes are admin-only; a document can belong to a group
(so every member's copilot searches it) even when the uploader is not in
that group. Existing rows inherit the uploader's current group_id.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kb_documents",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_kb_documents_group_id_groups",
        "kb_documents",
        "groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_kb_documents_group_id", "kb_documents", ["group_id"])
    op.execute(
        sa.text(
            """
            UPDATE kb_documents AS d
            SET group_id = u.group_id
            FROM users AS u
            WHERE d.owner_id = u.id
              AND u.group_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_kb_documents_group_id", table_name="kb_documents")
    op.drop_constraint("fk_kb_documents_group_id_groups", "kb_documents", type_="foreignkey")
    op.drop_column("kb_documents", "group_id")
