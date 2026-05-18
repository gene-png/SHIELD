"""v1.8 client portal: intake metadata + memberships + messaging + deliverables.

Revision ID: 0002_v18_client_portal
Revises: 0001_initial
Create Date: 2026-05-17

This migration is the schema half of the v1.8 client-portal rework.
It is intentionally additive — no existing column changes type or
nullability except `artifacts.client_id`, which is back-filled from
each artifact's project before being made NOT NULL.

Per UX doc round 2 §10 answer (Option B for storage paths):
  - `artifacts.project_id` stays NOT NULL.
  - The check constraint "project_id NULL only when stage='client_repository'"
    is NOT added.
  - Each existing Client gets one synthetic Project with
    is_client_repository=True; client-tier uploads (from the new
    /portal/documents endpoint, coming in PR 3) will land there with
    stage='client_repository'.

Per UX doc round 2 §10 answer (membership model):
  - `users.client_id` is NOT added. ClientMembership is the only
    user→client linkage, plus the new ReviewerAssignment for
    consulting-firm reviewers.

Backfill steps (in order):
  1. Add new columns to existing tables; backfill where required.
  2. Create the six new tables.
  3. Create one ClientMembership per existing CLIENT-role user,
     attached to the first Client (the demo seed has exactly one).
  4. Create one synthetic "Client Repository" Project per existing Client.
  5. Backfill `service_interests` on the demo Acme client so the
     post-migration demo state is "intake not yet completed, but
     all three services interesting" — matches the answer captured
     in the UX doc round 2 chat.

The DB trigger that enforces `artifacts.origin` immutability is
unchanged — origin and client_id are independent.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_v18_client_portal"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ============================================================
    # 1. Extend existing tables
    # ============================================================

    # --- clients: intake metadata --------------------------------
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("legal_name", sa.String(255)))
        batch.add_column(sa.Column("dba_name", sa.String(255)))
        batch.add_column(sa.Column("website", sa.String(255)))
        batch.add_column(sa.Column("size_band", sa.String(32)))
        batch.add_column(sa.Column("primary_poc_name", sa.String(255)))
        batch.add_column(sa.Column("primary_poc_title", sa.String(255)))
        batch.add_column(sa.Column("primary_poc_email", sa.String(255)))
        batch.add_column(sa.Column("primary_poc_phone", sa.String(64)))
        batch.add_column(sa.Column("address_line1", sa.String(255)))
        batch.add_column(sa.Column("address_line2", sa.String(255)))
        batch.add_column(sa.Column("city", sa.String(128)))
        batch.add_column(sa.Column("state", sa.String(64)))
        batch.add_column(sa.Column("postal_code", sa.String(32)))
        batch.add_column(sa.Column("country", sa.String(64), server_default="United States"))
        batch.add_column(sa.Column("compliance_frameworks", sa.JSON,
                                   nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("compliance_deadline", sa.Date))
        batch.add_column(sa.Column("prompting_context", sa.Text))
        batch.add_column(sa.Column("service_interests", sa.JSON,
                                   nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("consult_requested", sa.Boolean,
                                   nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("intake_completed_at", sa.DateTime))

    # --- users: optional richer profile --------------------------
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("title", sa.String(255)))
        batch.add_column(sa.Column("phone", sa.String(64)))

    # --- projects: synthetic client-repository flag -------------
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("is_client_repository", sa.Boolean,
                                   nullable=False, server_default=sa.false()))

    # --- artifacts: denormalized client_id ----------------------
    # Add as nullable first so the backfill UPDATE can run, then
    # NOT-NULL it once every row has a value.
    op.add_column("artifacts", sa.Column("client_id", sa.String(36)))
    op.execute("""
        UPDATE artifacts
           SET client_id = projects.client_id
          FROM projects
         WHERE artifacts.project_id = projects.id
           AND artifacts.client_id IS NULL
    """)
    with op.batch_alter_table("artifacts") as batch:
        batch.alter_column("client_id", nullable=False)
        batch.create_foreign_key(
            "fk_artifacts_client_id",
            "clients", ["client_id"], ["id"],
        )
        batch.create_index("ix_artifacts_client_id", ["client_id"])

    # ============================================================
    # 2. New tables
    # ============================================================

    op.create_table(
        "client_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36),
                  sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("membership_role", sa.String(32),
                  nullable=False, server_default="member"),
        sa.Column("invited_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("invited_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime),
        sa.UniqueConstraint("client_id", "user_id", name="uq_client_user"),
    )
    op.create_index("ix_client_memberships_user_id", "client_memberships", ["user_id"])

    op.create_table(
        "client_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("invited_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("accepted_at", sa.DateTime),
        sa.Column("revoked_at", sa.DateTime),
    )
    op.create_index("ix_client_invitations_client_id", "client_invitations", ["client_id"])
    op.create_index("ix_client_invitations_email", "client_invitations", ["email"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id")),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("read_at_map", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_messages_client_id_created_at",
                    "messages", ["client_id", "created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id")),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("link", sa.String(512)),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime),
    )
    op.create_index("ix_notifications_user_id_read_at",
                    "notifications", ["user_id", "read_at"])

    op.create_table(
        "deliverables",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("finalized_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("finalized_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("superseded_at", sa.DateTime),
        sa.Column("superseded_by", sa.String(36), sa.ForeignKey("deliverables.id")),
    )
    op.create_index("ix_deliverables_client_id", "deliverables", ["client_id"])

    op.create_table(
        "reviewer_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("assigned_by_id", sa.String(36),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime),
        sa.UniqueConstraint("reviewer_id", "client_id", name="uq_reviewer_client"),
    )
    op.create_index("ix_reviewer_assignments_reviewer_id",
                    "reviewer_assignments", ["reviewer_id"])

    # ============================================================
    # 3. Data backfill
    # ============================================================

    # --- Acme demo seed: all three services interesting, intake NOT
    #     completed (so client@demo walks the new welcome on next login).
    # Cast both sides to jsonb so the "is empty?" check works (Postgres
    # JSON has no `=` operator; jsonb does).
    op.execute("""
        UPDATE clients
           SET service_interests = '["tech_debt","zero_trust","attack_surface"]'
         WHERE name ILIKE 'Acme%%'
           AND (service_interests IS NULL
                OR service_interests::jsonb = '[]'::jsonb)
    """)

    # --- Synthetic "Client Repository" project per existing client.
    # We pick the first ADMIN user as the project creator (clients can't
    # create projects). If somehow there is no admin yet, leave
    # created_by_id NULL — the column allows it.
    op.execute("""
        WITH first_admin AS (
            SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1
        )
        INSERT INTO projects
            (id, client_id, platform, name, stage, is_client_repository,
             archived, created_at, created_by_id)
        SELECT
            md5(random()::text || c.id || clock_timestamp()::text)::text,
            c.id,
            'tech_debt',          -- arbitrary; the repo project's platform is unused
            'Client Repository',
            'client_repository',
            TRUE,
            FALSE,
            now(),
            (SELECT id FROM first_admin)
        FROM clients c
        WHERE NOT EXISTS (
            SELECT 1 FROM projects p
             WHERE p.client_id = c.id
               AND p.is_client_repository = TRUE
        )
    """)

    # --- ClientMembership for every existing CLIENT-role user.
    # The seed has exactly one CLIENT user (client@demo) and one client
    # (Acme). For more complex setups this would need per-tenant
    # mapping — out of scope here; admins can backfill manually.
    op.execute("""
        WITH the_client AS (
            SELECT id FROM clients ORDER BY created_at LIMIT 1
        )
        INSERT INTO client_memberships
            (id, client_id, user_id, membership_role, invited_at, accepted_at)
        SELECT
            md5(random()::text || u.id || clock_timestamp()::text)::text,
            (SELECT id FROM the_client),
            u.id,
            'primary_poc',
            now(),
            now()
        FROM users u
        WHERE u.role = 'client'
          AND NOT EXISTS (
            SELECT 1 FROM client_memberships m
             WHERE m.user_id = u.id
          )
    """)


def downgrade() -> None:
    # Drop new tables in reverse-dependency order.
    op.drop_index("ix_reviewer_assignments_reviewer_id", table_name="reviewer_assignments")
    op.drop_table("reviewer_assignments")

    op.drop_index("ix_deliverables_client_id", table_name="deliverables")
    op.drop_table("deliverables")

    op.drop_index("ix_notifications_user_id_read_at", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_messages_client_id_created_at", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_client_invitations_email", table_name="client_invitations")
    op.drop_index("ix_client_invitations_client_id", table_name="client_invitations")
    op.drop_table("client_invitations")

    op.drop_index("ix_client_memberships_user_id", table_name="client_memberships")
    op.drop_table("client_memberships")

    # Synthetic projects — remove only the rows we created.
    op.execute("DELETE FROM projects WHERE is_client_repository = TRUE")

    # Drop added columns. batch_alter_table so SQLite-friendly.
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_index("ix_artifacts_client_id")
        batch.drop_constraint("fk_artifacts_client_id", type_="foreignkey")
        batch.drop_column("client_id")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("is_client_repository")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("phone")
        batch.drop_column("title")

    with op.batch_alter_table("clients") as batch:
        for col in (
            "intake_completed_at", "consult_requested", "service_interests",
            "prompting_context", "compliance_deadline", "compliance_frameworks",
            "country", "postal_code", "state", "city",
            "address_line2", "address_line1",
            "primary_poc_phone", "primary_poc_email",
            "primary_poc_title", "primary_poc_name",
            "size_band", "website", "dba_name", "legal_name",
        ):
            batch.drop_column(col)
