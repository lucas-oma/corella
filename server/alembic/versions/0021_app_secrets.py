"""app_secrets: named, encrypted values for call-type hook headers

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-18

Admins manage these in Admin → Secrets. Call-type pre/post headers
store {{secret.NAME}} templates (returned on GET) and dispatch
interpolates the real value. The value itself is never returned.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_app_secrets_name", "app_secrets", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_app_secrets_name", table_name="app_secrets")
    op.drop_table("app_secrets")
