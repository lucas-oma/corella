from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CallType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Admin-managed call type — replaces the old hardcoded enum
    (meeting/sales/support/interview/one_on_one). Steers the post-call
    report's focus via `report_guidance` (appended to the report prompt,
    see app/services/copilot/report.py), and can optionally fire an
    external API call before a call of this type starts and/or after it
    finishes automatic post-call processing (see
    app/services/admin/call_hooks.py).
    """

    __tablename__ = "call_types"

    name: Mapped[str] = mapped_column(String(255))
    # Stable, admin-facing-but-machine-safe key — lowercase-hyphenated.
    # Not used for lookups anywhere in code (Meeting references a row by
    # id, not slug); kept mainly so a migration/export has a stable
    # identifier independent of a display-name rename.
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    report_guidance: Mapped[str | None] = mapped_column(Text)
    # Exactly one row should have this true at a time — enforced in
    # application code (app/api/admin.py), not a DB constraint: unsetting
    # every other row happens in the same transaction as setting this one.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    # Fired from create_meeting (app/api/meetings.py) — before the call/
    # chat session actually starts. GET by default (a lookup), but a
    # POST-with-body pre-call (e.g. a CRM query) is supported too, hence
    # pre_call_body_template existing symmetrically with the post side.
    pre_call_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pre_call_url: Mapped[str | None] = mapped_column(String(2048))
    pre_call_method: Mapped[str] = mapped_column(String(16), default="GET")
    # Encrypted with the same Fernet key derived from jwt_secret every
    # other credential in this app uses (app.core.security) — headers
    # commonly carry an Authorization value. Write-only, never returned
    # by the API after saving, same convention as every other secret.
    pre_call_headers_encrypted: Mapped[str | None] = mapped_column(Text)
    # Raw JSON text with {{placeholder}} tokens — see
    # app/services/admin/call_hooks.py:render_template for the
    # substitution rules and the supported placeholder list.
    pre_call_body_template: Mapped[str | None] = mapped_column(Text)
    # The special pre-call feature: when true, the response body this
    # call fetches is stored on Meeting.pre_call_context and fed into the
    # live copilot's prompt alongside the regular knowledge-base context
    # (app/services/copilot/live.py:run_cycle) — not just logged/ignored.
    pre_call_use_as_context: Mapped[bool] = mapped_column(Boolean, default=False)

    # Fired once from _generate_report_async (app/workers/tasks.py) after
    # a successful auto-generated report — the original single "webhook"
    # this app had, now the "post" half of a pre/post pair.
    post_call_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    post_call_url: Mapped[str | None] = mapped_column(String(2048))
    post_call_method: Mapped[str] = mapped_column(String(16), default="POST")
    post_call_headers_encrypted: Mapped[str | None] = mapped_column(Text)
    post_call_body_template: Mapped[str | None] = mapped_column(Text)
    # The special post-call feature: when true, the body_template above
    # is ignored entirely and the full structured payload (transcript,
    # report, CopilotInsight suggestion/blocker/score timeline, action
    # items with status — everything) is sent as JSON instead — see
    # app/services/admin/call_hooks.py:build_full_payload.
    post_call_send_full_payload: Mapped[bool] = mapped_column(Boolean, default=False)
