"""v1.8 round-7 §19: User.auto_progress_workflows opt-out flag.

Revision ID: 0006_v18r7_auto_progress
Revises:    0005_v18r7_user_timezone
Create Date: 2026-05-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_v18r7_auto_progress"
down_revision = "0005_v18r7_user_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column(
            "auto_progress_workflows", sa.Boolean,
            nullable=False, server_default=sa.true(),
        ))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auto_progress_workflows")
