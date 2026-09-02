import enum
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enum_types import pg_enum
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import User
from app.models.voice_identity import VoiceIdentity


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
    coach_score: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)

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
    """A speaker identity, scoped to one meeting's own online clustering
    (Phase F/F-2) — `label` ("Speaker 2"/"Them 3") is always set as the
    anonymous fallback. `voice_identity_id` (Phase O) is set once this
    cluster is matched or newly resolved against the durable,
    cross-meeting VoiceIdentity library — when present, its
    display_name takes precedence over `label` for rendering.
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
    voice_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_identities.id", ondelete="SET NULL")
    )

    voice_identity: Mapped[VoiceIdentity | None] = relationship(lazy="joined")

    @property
    def display_label(self) -> str:
        """label, unless a resolved cross-meeting identity overrides it."""
        if self.voice_identity is not None and self.voice_identity.display_name:
            return self.voice_identity.display_name
        return self.label

    @property
    def linked_user_id(self) -> uuid.UUID | None:
        """Set only when this speaker's resolved identity is an enrolled
        account, not an anonymous recognized-by-name guest — lets the
        frontend render "Me" only for the viewer's own linked identity,
        the real name otherwise (see TranscriptSegmentRead)."""
        return self.voice_identity.linked_user_id if self.voice_identity else None


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
        display label via the `speaker_id` relationship above. Prefers a
        resolved cross-meeting VoiceIdentity name (Phase O) over the
        anonymous per-meeting "Speaker N"/"Them N" fallback.
        """
        return self.speaker.display_label if self.speaker else None

    @property
    def linked_user_id(self) -> uuid.UUID | None:
        """Set only when this segment's resolved identity is an enrolled
        account — lets the frontend render "Me" for the viewer's own
        linked identity and the real name otherwise (Phase O)."""
        return self.speaker.linked_user_id if self.speaker else None


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
