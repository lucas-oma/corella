"""user AI preferences

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    llm_provider = sa.Enum(
        "anthropic", "openai", "gemini", "ollama", name="user_preferred_llm_provider"
    )
    llm_provider.create(op.get_bind(), checkfirst=True)
    op.add_column("users", sa.Column("preferred_llm_provider", llm_provider, nullable=True))
    op.add_column("users", sa.Column("preferred_llm_model", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("preferred_stt_provider", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("preferred_stt_model", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("preferred_stt_language", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferred_stt_language")
    op.drop_column("users", "preferred_stt_model")
    op.drop_column("users", "preferred_stt_provider")
    op.drop_column("users", "preferred_llm_model")
    op.drop_column("users", "preferred_llm_provider")
    sa.Enum(name="user_preferred_llm_provider").drop(op.get_bind(), checkfirst=True)
