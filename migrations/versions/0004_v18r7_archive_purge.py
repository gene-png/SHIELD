"""v1.8 round-7 §17: archive + purge lifecycle for Project and Artifact.

Revision ID: 0004_v18r7_archive_purge
Revises:    0003_v18r3_service_requests
Create Date: 2026-05-18

Adds the two-tier soft-delete pattern: archive (reversible, default
verb) and purge (hard-delete tombstone, high-friction admin verb).

  projects        : already had `archived` bool. Adds archived_at,
                    archived_by_id, archived_reason and the purged_*
                    triad.
  artifacts       : adds the full archive + purge field set.

All columns nullable so the rollout is backwards-compatible — every
existing row gets NULLs and continues to behave as 'active'.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_v18r7_archive_purge"
down_revision = "0003_v18r3_service_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("archived_at",     sa.DateTime))
        batch.add_column(sa.Column("archived_by_id",  sa.String(36),
                                   sa.ForeignKey("users.id")))
        batch.add_column(sa.Column("archived_reason", sa.Text))
        batch.add_column(sa.Column("purged_at",       sa.DateTime))
        batch.add_column(sa.Column("purged_by_id",    sa.String(36),
                                   sa.ForeignKey("users.id")))
        batch.add_column(sa.Column("purged_reason",   sa.Text))

    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("archived",        sa.Boolean,
                                   nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("archived_at",     sa.DateTime))
        batch.add_column(sa.Column("archived_by_id",  sa.String(36),
                                   sa.ForeignKey("users.id")))
        batch.add_column(sa.Column("archived_reason", sa.Text))
        batch.add_column(sa.Column("purged_at",       sa.DateTime))
        batch.add_column(sa.Column("purged_by_id",    sa.String(36),
                                   sa.ForeignKey("users.id")))
        batch.add_column(sa.Column("purged_reason",   sa.Text))


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        for col in ("purged_reason", "purged_by_id", "purged_at",
                    "archived_reason", "archived_by_id", "archived_at",
                    "archived"):
            batch.drop_column(col)

    with op.batch_alter_table("projects") as batch:
        for col in ("purged_reason", "purged_by_id", "purged_at",
                    "archived_reason", "archived_by_id", "archived_at"):
            batch.drop_column(col)
