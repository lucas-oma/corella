import enum
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User


class MeetingStatus(str, enum.Enum):
    RECORDING = "recording"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Channel(str, enum.Enum):
    ME = "me"
    THEM = "them"
    UNKNOWN = "unknown"


class ActionItemStatus(str, enum.Enum):
    OPEN = "open"
    DONE = "done"


class CallType(str, enum.Enum):
    """What kind of call this is — picked at creation time, used to steer
    the post-call report's focus (app/services/copilot/report.py's
    _CALL_TYPE_GUIDANCE), not just cosmetic."""

    MEETING = "meeting"
    SALES = "sales"
    SUPPORT = "support"
    INTERVIEW = "interview"
    ONE_ON_ONE = "one_on_one"


class Meeting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "meetings"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    call_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("call_profiles.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(255), default="Untitled meeting")
    status: Mapped[MeetingStatus] = mapped_column(
        pg_enum(MeetingStatus, "meeting_status"), default=MeetingStatus.RECORDING
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    audio_path: Mapped[str | None] = mapped_column(String(1024))
    summary: Mapped[str | None] = mapped_column(Text)
    processing_error: Mapped[str | None] = mapped_column(Text)
    call_type: Mapped[CallType] = mapped_column(
        pg_enum(CallType, "meeting_call_type"), default=CallType.MEETING
    )
    key_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    sentiment: Mapped[str | None] = mapped_column(String(255))
    notable_quotes: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    owner: Mapped[User] = relationship(lazy="joined")

    @property
    def has_audio(self) -> bool:
        """Not a column — lets MeetingRead expose whether audio exists
        without leaking the on-disk `audio_path` to the API.
        """
        return self.audio_path is not None

    @property
    def owner_name(self) -> str:
        """Not a column — lets MeetingRead show whose meeting this is, since
        a group member can now view another member's report."""
        return self.owner.full_name


class Speaker(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A speaker identity, scoped to the owning user so voices can be
    recognized across meetings once diarization embeddings are wired up.
    """

    __tablename__ = "speakers"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(String(255), default="Unknown speaker")
    channel: Mapped[Channel] = mapped_column(
        pg_enum(Channel, "speaker_channel"), default=Channel.UNKNOWN
    )
    # Point ID of this speaker's voice embedding in the Qdrant speaker_embeddings
    # collection, used for cross-meeting "remember voices" matching.
    embedding_ref: Mapped[str | None] = mapped_column(String(64))


class TranscriptSegment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transcript_segments"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    speaker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("speakers.id", ondelete="SET NULL")
    )
    channel: Mapped[Channel] = mapped_column(
        pg_enum(Channel, "segment_channel"), default=Channel.UNKNOWN
    )
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    speaker: Mapped[Speaker | None] = relationship(lazy="joined")

    @property
    def speaker_label(self) -> str | None:
        """Not a column — lets TranscriptSegmentRead expose the speaker's
        display name via the `speaker_id` relationship above.
        """
        return self.speaker.label if self.speaker else None


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notes"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text)


class ActionItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "action_items"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[ActionItemStatus] = mapped_column(
        pg_enum(ActionItemStatus, "action_item_status"), default=ActionItemStatus.OPEN
    )
