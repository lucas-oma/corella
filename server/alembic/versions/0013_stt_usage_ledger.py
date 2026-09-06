"""stt usage ledger: llm_usage_events.provider loosened to plain string,
audio_seconds column added, UsageKind gains stt_upload/stt_live

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-06

Deepgram STT usage now shares the same ledger/atomic-total machinery as
LLM usage (app/services/copilot/cost.py:add_meeting_cost) — priced by
audio duration instead of tokens. `provider` was a pg_enum bound to
LLMProvider (anthropic/openai/gemini/ollama only); Deepgram isn't an
LLMProvider (see Phase Q's own reasoning for why STT credentials are a
separate model from LLM ones) and a value column that needs a migration
every time a new provider shows up here isn't worth it long-term, so it's
loosened to a plain string instead.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE usage_event_kind ADD VALUE IF NOT EXISTS 'stt_upload'"))
    op.execute(sa.text("ALTER TYPE usage_event_kind ADD VALUE IF NOT EXISTS 'stt_live'"))

    op.alter_column(
        "llm_usage_events",
        "provider",
        type_=sa.String(length=50),
        postgresql_using="provider::text",
    )
    op.execute(sa.text("DROP TYPE IF EXISTS usage_event_provider"))

    op.add_column("llm_usage_events", sa.Column("audio_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    # Lossy for any row whose provider isn't one of the original four
    # (i.e. any real Deepgram STT row created after this migration) —
    # those get folded to 'ollama' as an arbitrary, clearly-wrong-looking
    # placeholder rather than silently mis-attributed to a real LLM
    # provider; same lossy-downgrade precedent as migration 0012's
    # call_type_id -> enum downgrade for a custom (post-migration) type.
    op.drop_column("llm_usage_events", "audio_seconds")

    op.execute(sa.text("CREATE TYPE usage_event_provider AS ENUM ('anthropic', 'openai', 'gemini', 'ollama')"))
    op.execute(
        sa.text(
            "ALTER TABLE llm_usage_events ALTER COLUMN provider TYPE usage_event_provider "
            "USING (CASE WHEN provider IN ('anthropic', 'openai', 'gemini', 'ollama') "
            "THEN provider ELSE 'ollama' END)::usage_event_provider"
        )
    )
    # Postgres has no DROP VALUE for enums — stt_upload/stt_live stay
    # defined but simply unused after downgrade, same as any other
    # forward-only enum addition in this project's migration history.
