"""v1.9: archive + purge lifecycle for CapabilityList.

Revision ID: 0007_v19_capability_list_lifecycle
Revises:    0006_v18r7_auto_progress
Create Date: 2026-05-18

Mirrors the field set Project + Artifact got in 0004. Nullable
additions so the rollout is backwards-compatible — every existing
row gets NULLs and continues to behave as 'active'.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_v19_cl_lifecycle"
down_revision = "0006_v18r7_auto_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("capability_lists") as batch:
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
    with op.batch_alter_table("capability_lists") as batch:
        for col in ("purged_reason", "purged_by_id", "purged_at",
                    "archived_reason", "archived_by_id", "archived_at",
                    "archived"):
            batch.drop_column(col)
