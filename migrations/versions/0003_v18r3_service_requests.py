"""v1.8 round-3: service_requests + Project.client_display_name + backfill.

Revision ID: 0003_v18r3_service_requests
Revises: 0002_v18_client_portal
Create Date: 2026-05-17

Two things this adds:
  1. service_requests table (the "client REQUESTS a service" pattern
     from round-3 §4.3). Replaces the never-implemented "Start a Project"
     button with a request that admin can fulfill or decline.
  2. projects.client_display_name (round-3 §4.4) — optional friendly
     label the portal renders instead of `name`.

Backfill, per the round-3 chat answer "Backfill ServiceRequests for
them": for each existing non-repository Acme project, create one
fulfilled ServiceRequest pointing at it. That way the dashboard
state-machine cards in PR 3B render the existing demo projects as
the natural transition (Requested → fulfilled → active), not as
mystery projects the client never asked for.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_v18r3_service_requests"
down_revision = "0002_v18_client_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. projects.client_display_name -----------------------------
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("client_display_name", sa.String(255)))

    # ---- 2. service_requests ----------------------------------------
    op.create_table(
        "service_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36),
                  sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("requested_by", sa.String(36),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("deadline", sa.Date),
        sa.Column("requested_at", sa.DateTime,
                  nullable=False, server_default=sa.func.now()),
        sa.Column("fulfilled_project_id", sa.String(36),
                  sa.ForeignKey("projects.id")),
        sa.Column("declined_at", sa.DateTime),
        sa.Column("declined_reason", sa.Text),
    )
    op.create_index("ix_service_requests_client_id",
                    "service_requests", ["client_id"])
    op.create_index("ix_service_requests_fulfilled_project_id",
                    "service_requests", ["fulfilled_project_id"])

    # ---- 3. Backfill fulfilled requests for existing Acme projects --
    # For every non-repository, non-archived project we create one
    # fulfilled ServiceRequest authored by the project's created_by_id
    # (or by an arbitrary admin if created_by_id is NULL). `service`
    # maps from Project.platform.
    op.execute("""
        WITH first_admin AS (
            SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1
        )
        INSERT INTO service_requests
            (id, client_id, requested_by, service, notes,
             requested_at, fulfilled_project_id)
        SELECT
            md5(random()::text || p.id || clock_timestamp()::text)::text,
            p.client_id,
            COALESCE(p.created_by_id, (SELECT id FROM first_admin)),
            p.platform::text,
            'Backfilled from seed project ' || p.name,
            p.created_at,
            p.id
        FROM projects p
        WHERE p.is_client_repository = FALSE
          AND p.archived = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM service_requests sr
              WHERE sr.fulfilled_project_id = p.id
          )
    """)


def downgrade() -> None:
    op.drop_index("ix_service_requests_fulfilled_project_id",
                  table_name="service_requests")
    op.drop_index("ix_service_requests_client_id",
                  table_name="service_requests")
    op.drop_table("service_requests")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("client_display_name")
