"""organizations, memberships, invites, group_memberships, org_id columns

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-19

Replaces global User.role / User.group_id with instance super-admin plus
org-scoped memberships. Existing rows (if any) wrap into one instance org.
"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    org_role = postgresql.ENUM("owner", "admin", "member", name="org_role", create_type=False)
    org_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_instance_org", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "uq_one_instance_org",
        "organizations",
        ["is_instance_org"],
        unique=True,
        postgresql_where=sa.text("is_instance_org"),
    )

    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", postgresql.ENUM("owner", "admin", "member", name="org_role", create_type=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])
    op.create_unique_constraint(
        "uq_org_membership_user", "organization_memberships", ["user_id", "organization_id"]
    )
    op.create_index(
        "uq_org_one_owner",
        "organization_memberships",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )

    op.create_table(
        "organization_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", postgresql.ENUM("owner", "admin", "member", name="org_role", create_type=False), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_organization_invites_organization_id", "organization_invites", ["organization_id"])
    op.create_index("ix_organization_invites_email", "organization_invites", ["email"])
    op.create_index("ix_organization_invites_token_hash", "organization_invites", ["token_hash"], unique=True)
    op.create_index(
        "uq_org_pending_invite_email",
        "organization_invites",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )

    op.create_table(
        "group_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_group_memberships_user_id", "group_memberships", ["user_id"])
    op.create_index("ix_group_memberships_group_id", "group_memberships", ["group_id"])
    op.create_unique_constraint("uq_group_membership_user", "group_memberships", ["user_id", "group_id"])

    op.add_column("users", sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("active_organization_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.add_column("groups", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("meetings", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("kb_documents", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("voice_identities", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("call_types", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("app_secrets", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("api_keys", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("llm_usage_events", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))

    conn = op.get_bind()
    org_id = uuid.uuid4()
    admin_row = conn.execute(
        sa.text(
            "SELECT id, full_name FROM users WHERE role = 'admin' ORDER BY created_at ASC LIMIT 1"
        )
    ).first()
    any_user = conn.execute(
        sa.text("SELECT id, full_name FROM users ORDER BY created_at ASC LIMIT 1")
    ).first()
    org_name = "Corella Org"
    if admin_row is not None:
        org_name = f"{admin_row.full_name} Org"
    elif any_user is not None:
        org_name = f"{any_user.full_name} Org"

    conn.execute(
        sa.text(
            "INSERT INTO organizations (id, name, is_instance_org) "
            "VALUES (:id, :name, true)"
        ),
        {"id": org_id, "name": org_name},
    )

    conn.execute(
        sa.text("UPDATE groups SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE meetings SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE kb_documents SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE voice_identities SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE call_types SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE app_secrets SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE api_keys SET organization_id = :org_id"),
        {"org_id": org_id},
    )
    conn.execute(
        sa.text("UPDATE llm_usage_events SET organization_id = :org_id"),
        {"org_id": org_id},
    )

    users = conn.execute(sa.text("SELECT id, role, group_id FROM users ORDER BY created_at ASC")).all()
    owner_assigned = False
    for user in users:
        if user.role == "admin":
            conn.execute(
                sa.text("UPDATE users SET is_super_admin = true WHERE id = :id"),
                {"id": user.id},
            )
            membership_role = "member"
            if not owner_assigned:
                membership_role = "owner"
                owner_assigned = True
            else:
                membership_role = "admin"
        else:
            membership_role = "member"
        conn.execute(
            sa.text(
                "INSERT INTO organization_memberships (id, user_id, organization_id, role) "
                "VALUES (:id, :user_id, :org_id, :role)"
            ),
            {
                "id": uuid.uuid4(),
                "user_id": user.id,
                "org_id": org_id,
                "role": membership_role,
            },
        )
        if user.group_id is not None:
            conn.execute(
                sa.text(
                    "INSERT INTO group_memberships (id, user_id, group_id) "
                    "VALUES (:id, :user_id, :group_id)"
                ),
                {"id": uuid.uuid4(), "user_id": user.id, "group_id": user.group_id},
            )

    conn.execute(
        sa.text("UPDATE users SET active_organization_id = :org_id"),
        {"org_id": org_id},
    )

    op.create_foreign_key(
        "fk_users_active_organization_id",
        "users",
        "organizations",
        ["active_organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_groups_organization_id",
        "groups",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_meetings_organization_id",
        "meetings",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_kb_documents_organization_id",
        "kb_documents",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_voice_identities_organization_id",
        "voice_identities",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_call_types_organization_id",
        "call_types",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_app_secrets_organization_id",
        "app_secrets",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_api_keys_organization_id",
        "api_keys",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_llm_usage_events_organization_id",
        "llm_usage_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("groups", "organization_id", nullable=False)
    op.alter_column("meetings", "organization_id", nullable=False)
    op.alter_column("kb_documents", "organization_id", nullable=False)
    op.alter_column("voice_identities", "organization_id", nullable=False)
    op.alter_column("call_types", "organization_id", nullable=False)
    op.alter_column("app_secrets", "organization_id", nullable=False)
    op.alter_column("api_keys", "organization_id", nullable=False)

    op.create_index("ix_groups_organization_id", "groups", ["organization_id"])
    op.create_index("ix_meetings_organization_id", "meetings", ["organization_id"])
    op.create_index("ix_kb_documents_organization_id", "kb_documents", ["organization_id"])
    op.create_index("ix_voice_identities_organization_id", "voice_identities", ["organization_id"])
    op.create_index("ix_call_types_organization_id", "call_types", ["organization_id"])
    op.create_index("ix_app_secrets_organization_id", "app_secrets", ["organization_id"])
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])
    op.create_index("ix_llm_usage_events_organization_id", "llm_usage_events", ["organization_id"])

    op.drop_constraint("call_types_slug_key", "call_types", type_="unique")
    op.create_unique_constraint("uq_call_types_org_slug", "call_types", ["organization_id", "slug"])

    op.drop_index("ix_app_secrets_name", table_name="app_secrets")
    op.create_index("ix_app_secrets_name", "app_secrets", ["name"], unique=False)
    op.create_unique_constraint("uq_app_secrets_org_name", "app_secrets", ["organization_id", "name"])

    op.drop_constraint("users_group_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "group_id")
    op.drop_column("users", "role")
    op.execute("DROP TYPE user_role")


def downgrade() -> None:
    user_role = postgresql.ENUM("admin", "member", name="user_role", create_type=False)
    user_role.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column("role", postgresql.ENUM("admin", "member", name="user_role", create_type=False), nullable=False, server_default="member"),
    )
    op.add_column(
        "users",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="SET NULL"), nullable=True),
    )
    op.execute(
        "UPDATE users SET role = 'admin' WHERE is_super_admin"
    )

    op.drop_constraint("uq_app_secrets_org_name", "app_secrets", type_="unique")
    op.drop_index("ix_app_secrets_name", table_name="app_secrets")
    op.create_index("ix_app_secrets_name", "app_secrets", ["name"], unique=True)

    op.drop_constraint("uq_call_types_org_slug", "call_types", type_="unique")
    op.create_unique_constraint("call_types_slug_key", "call_types", ["slug"])

    for table, fk, col in (
        ("llm_usage_events", "fk_llm_usage_events_organization_id", "organization_id"),
        ("api_keys", "fk_api_keys_organization_id", "organization_id"),
        ("app_secrets", "fk_app_secrets_organization_id", "organization_id"),
        ("call_types", "fk_call_types_organization_id", "organization_id"),
        ("voice_identities", "fk_voice_identities_organization_id", "organization_id"),
        ("kb_documents", "fk_kb_documents_organization_id", "organization_id"),
        ("meetings", "fk_meetings_organization_id", "organization_id"),
        ("groups", "fk_groups_organization_id", "organization_id"),
        ("users", "fk_users_active_organization_id", "active_organization_id"),
    ):
        op.drop_constraint(fk, table, type_="foreignkey")
        if col != "active_organization_id":
            op.drop_index(f"ix_{table}_{col}", table_name=table)
        op.drop_column(table, col)

    op.drop_column("users", "is_super_admin")

    op.drop_table("group_memberships")
    op.drop_table("organization_invites")
    op.drop_table("organization_memberships")
    op.drop_table("organizations")
    op.execute("DROP TYPE org_role")
