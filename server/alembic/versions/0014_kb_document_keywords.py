"""kb_documents.keywords + usage_event_kind gains kb_extraction

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-07

KB documents now get an LLM-extracted list of proper nouns/acronyms/jargon
at upload time (app/workers/tasks.py:process_kb_document), stored directly
on the document rather than re-derived on every transcription request — see
app/services/embeddings/kb_keywords.py. Feeds Deepgram's `keywords` param /
faster-whisper's `initial_prompt` (app/services/access.py:searchable_kb_keywords)
to bias STT toward whatever a group's knowledge base actually talks about.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kb_documents", sa.Column("keywords", postgresql.ARRAY(sa.String()), nullable=True))
    op.execute(sa.text("ALTER TYPE usage_event_kind ADD VALUE IF NOT EXISTS 'kb_extraction'"))


def downgrade() -> None:
    op.drop_column("kb_documents", "keywords")
    # Postgres has no DROP VALUE for enums — kb_extraction stays defined but
    # simply unused after downgrade, same as every other forward-only enum
    # addition in this project's migration history (see 0013's own note).
