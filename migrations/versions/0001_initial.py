"""Initial schema + origin immutability trigger.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-16

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


user_role     = postgresql.ENUM("client", "admin", "reviewer", name="user_role", create_type=False)
origin_enum   = postgresql.ENUM("human_input", "ai_generated", "human_ai_informed", name="origin", create_type=False)
reuse_status  = postgresql.ENUM("draft", "approved", "superseded", name="reuse_status", create_type=False)
trust_tier    = postgresql.ENUM(
    "client_asserted", "admin_assisted", "admin_entered_on_behalf",
    "client_provided_evidence", "not_applicable", name="trust_tier", create_type=False,
)
platform_type = postgresql.ENUM("tech_debt", "zero_trust", "attack_surface", name="platform_type", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    origin_enum.create(bind, checkfirst=True)
    reuse_status.create(bind, checkfirst=True)
    trust_tier.create(bind, checkfirst=True)
    platform_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sub", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active_flag", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "clients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("industry", sa.String(120)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "capability_lists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("origin", origin_enum, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("client_id", "version", name="uq_client_version"),
    )

    op.create_table(
        "capability_list_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("capability_list_id", sa.String(36), sa.ForeignKey("capability_lists.id"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("vendor", sa.String(255)),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("function", sa.String(255)),
        sa.Column("annual_cost_usd", sa.Integer),
        sa.Column("license_count", sa.Integer),
        sa.Column("notes", sa.Text),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("platform", platform_type, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(120), nullable=False, server_default="intake"),
        sa.Column("capability_list_version_id", sa.String(36), sa.ForeignKey("capability_lists.id")),
        sa.Column("framework", sa.String(120)),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("stage", sa.String(120), nullable=False),
        sa.Column("origin", origin_enum, nullable=False),
        sa.Column("trust_tier", trust_tier, nullable=False, server_default="not_applicable"),
        sa.Column("reuse_status", reuse_status, nullable=False, server_default="draft"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255)),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("storage_key", sa.String(512)),
        sa.Column("body_text", sa.Text),
        sa.Column("lineage", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("actor_role", user_role),
        sa.Column("capability_list_version_id", sa.String(36), sa.ForeignKey("capability_lists.id")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("promoted_at", sa.DateTime),
        sa.Column("promoted_by_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("promotion_reason", sa.Text),
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("at", sa.DateTime, nullable=False, server_default=sa.text("now()"), index=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("actor_email", sa.String(255)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(120)),
        sa.Column("target_id", sa.String(36)),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id")),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id")),
        sa.Column("details", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )

    op.create_table(
        "questionnaire_responses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("framework", sa.String(120), nullable=False),
        sa.Column("control_id", sa.String(120), nullable=False, index=True),
        sa.Column("answer", sa.String(40), nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("evidence_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id")),
        sa.Column("trust_tier", trust_tier, nullable=False),
        sa.Column("attributed_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("submitted_at", sa.DateTime),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "mitre_techniques",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("technique_id", sa.String(40), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tactic", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("matrix", sa.String(60), nullable=False, server_default="enterprise"),
    )

    op.create_table(
        "coverage_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("capability_list_version_id", sa.String(36), sa.ForeignKey("capability_lists.id"),
                  nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id")),
        sa.Column("summary", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "coverage_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("coverage_run_id", sa.String(36), sa.ForeignKey("coverage_runs.id"),
                  nullable=False, index=True),
        sa.Column("technique_id", sa.String(40), nullable=False),
        sa.Column("coverage", sa.String(40), nullable=False),
        sa.Column("detection_tools", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("prevention_tools", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("response_tools", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("rationale", sa.Text),
    )

    # ----------------------------------------------------------------
    # Origin immutability trigger.
    #
    # The application enforces this at the spine layer too, but a DB
    # trigger means it cannot be silently subverted by a stray UPDATE.
    # ----------------------------------------------------------------
    op.execute("""
    CREATE OR REPLACE FUNCTION artifacts_origin_immutable()
    RETURNS TRIGGER AS $$
    BEGIN
        IF OLD.origin IS DISTINCT FROM NEW.origin THEN
            RAISE EXCEPTION 'artifact.origin is immutable (id=%)', OLD.id;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_artifacts_origin_immutable
    BEFORE UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION artifacts_origin_immutable();
    """)

    # Audit entries are append-only.
    op.execute("""
    CREATE OR REPLACE FUNCTION audit_entries_append_only()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_entries is append-only';
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_audit_append_only
    BEFORE UPDATE OR DELETE ON audit_entries
    FOR EACH ROW EXECUTE FUNCTION audit_entries_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_entries;")
    op.execute("DROP FUNCTION IF EXISTS audit_entries_append_only();")
    op.execute("DROP TRIGGER IF EXISTS trg_artifacts_origin_immutable ON artifacts;")
    op.execute("DROP FUNCTION IF EXISTS artifacts_origin_immutable();")

    for table in [
        "coverage_findings", "coverage_runs", "mitre_techniques",
        "questionnaire_responses", "audit_entries", "artifacts",
        "projects", "capability_list_items", "capability_lists",
        "clients", "users",
    ]:
        op.drop_table(table)

    bind = op.get_bind()
    platform_type.drop(bind, checkfirst=True)
    trust_tier.drop(bind, checkfirst=True)
    reuse_status.drop(bind, checkfirst=True)
    origin_enum.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
