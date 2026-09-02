import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class VoiceIdentity(UUIDPrimaryKeyMixin, Base):
    """A durable, cross-meeting voice identity — what Phase F/F-2's
    per-meeting Speaker rows get linked to once a voice is recognized
    across meetings (Speaker.voice_identity_id). Two ways one of these
    comes to exist:

    - Self-enrollment (app/api/auth.py: POST /api/auth/me/voice) —
      linked_user_id set, display_name a snapshot of the user's full_name
      at enrollment time (not live-derived — a later profile rename
      doesn't retroactively reshuffle disambiguation suffixes already
      assigned to other identities in the group).
    - Live LLM name-spotting (corella.identify_speaker_name) — an
      anonymous recurring voice whose owner isn't a Corella account at
      all, named from what it said in a call. linked_user_id stays null.

    group_id=NULL is a deliberate, meaningful state — it means "private,
    scoped to one ungrouped user's own self-recognition only," not
    "shared with nobody in particular." Recognition search
    (search_speaker_embeddings) always scopes by this same group_id, so a
    private identity is never visible outside the one account it belongs
    to, matching the same group-scoping precedent Phase H already
    established for KB sharing.
    """

    __tablename__ = "voice_identities"

    # No separate embedding_ref column — this row's own id doubles as the
    # point id in the Qdrant speaker_embeddings collection (one identity,
    # exactly one embedding, no need for a redundant indirection).
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), index=True
    )
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
