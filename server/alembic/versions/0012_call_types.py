"""admin-managed call types

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-03

"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The exact guidance text from the old hardcoded
# app/services/copilot/report.py::_CALL_TYPE_GUIDANCE, seeded onto the
# matching row so existing behavior is unchanged until an admin edits it.
_SEED_TYPES = [
    (
        "meeting",
        "Meeting",
        "This is a general meeting. Focus the summary and key_topics on decisions made and open "
        "questions left unresolved.",
        True,
    ),
    (
        "sales",
        "Sales call",
        "This is a sales call. Focus the summary and key_topics on the prospect's pain points, "
        "objections raised, budget/timeline signals, and next steps or deal stage. sentiment should "
        "reflect how receptive the prospect seemed. Prioritize quotes about pricing, timeline, or "
        "objections for notable_quotes.",
        False,
    ),
    (
        "support",
        "Support call",
        "This is a customer support call. Focus the summary and key_topics on the issue reported, "
        "whether it was resolved, and any escalation risk. sentiment should reflect the customer's "
        "frustration or satisfaction level. Prioritize quotes describing the problem or the "
        "resolution for notable_quotes.",
        False,
    ),
    (
        "interview",
        "Interview",
        "This is a job interview. Focus the summary and key_topics on the candidate's strengths, "
        "gaps, and fit signals relative to what was asked. sentiment should reflect how the "
        "conversation went overall. Prioritize quotes that reveal candidate strengths or concerns "
        "for notable_quotes.",
        False,
    ),
    (
        "one_on_one",
        "1:1",
        "This is a one-on-one check-in. Focus the summary and key_topics on blockers raised, growth "
        "or career topics, and commitments made by either person. sentiment should reflect the "
        "overall tone of the conversation. Prioritize quotes about blockers or commitments for "
        "notable_quotes.",
        False,
    ),
]


def upgrade() -> None:
    op.create_table(
        "call_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("report_guidance", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("webhook_url", sa.String(2048), nullable=True),
        sa.Column("webhook_method", sa.String(16), nullable=False, server_default="POST"),
        sa.Column("webhook_headers_encrypted", sa.Text(), nullable=True),
        sa.Column("webhook_body_template", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_call_types_slug", "call_types", ["slug"])

    conn = op.get_bind()
    call_types = sa.table(
        "call_types",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("report_guidance", sa.Text),
        sa.column("is_default", sa.Boolean),
    )
    seed_ids = {}
    for slug, name, guidance, is_default in _SEED_TYPES:
        row_id = uuid.uuid4()
        seed_ids[slug] = row_id
        conn.execute(
            call_types.insert().values(
                id=row_id, name=name, slug=slug, report_guidance=guidance, is_default=is_default
            )
        )

    op.add_column("meetings", sa.Column("call_type_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_meetings_call_type_id", "meetings", "call_types", ["call_type_id"], ["id"], ondelete="SET NULL"
    )

    # Backfill: the old enum column's text value *is* the new slug for
    # every seeded row, so this is a direct value match, one UPDATE per
    # seed row rather than a join (simplest given only 5 rows exist).
    for slug, row_id in seed_ids.items():
        conn.execute(
            sa.text("UPDATE meetings SET call_type_id = :row_id WHERE call_type = :slug"),
            {"row_id": str(row_id), "slug": slug},
        )

    op.drop_column("meetings", "call_type")
    sa.Enum(name="meeting_call_type").drop(conn, checkfirst=True)


def downgrade() -> None:
    call_type_enum = sa.Enum(
        "meeting", "sales", "support", "interview", "one_on_one", name="meeting_call_type"
    )
    call_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "meetings",
        sa.Column("call_type", call_type_enum, nullable=False, server_default="meeting"),
    )

    conn = op.get_bind()
    # Best-effort: any meeting whose call_type_id points at a slug that
    # isn't one of the original 5 (an admin-added custom type has no enum
    # equivalent to go back to) falls back to 'meeting' — a genuine, known
    # lossy edge of this downgrade, not a forward-migration concern.
    known_slugs = [slug for slug, *_ in _SEED_TYPES]
    conn.execute(
        sa.text(
            "UPDATE meetings SET call_type = ct.slug::meeting_call_type "
            "FROM call_types ct "
            "WHERE meetings.call_type_id = ct.id AND ct.slug = ANY(:slugs)"
        ),
        {"slugs": known_slugs},
    )

    op.drop_constraint("fk_meetings_call_type_id", "meetings", type_="foreignkey")
    op.drop_column("meetings", "call_type_id")
    op.drop_index("ix_call_types_slug", table_name="call_types")
    op.drop_table("call_types")
