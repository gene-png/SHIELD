"""v1.8 round-7 §18: User.timezone (IANA string) for client-side
local-time rendering.

Revision ID: 0005_v18r7_user_timezone
Revises:    0004_v18r7_archive_purge
Create Date: 2026-05-18

The server stores + audits + wires everything as UTC. This column is
a render-time hint — the client-side local-time.js converts
`<time datetime="...Z">` elements to the viewer's local timezone. The
column is nullable; the JS defaults to the browser's auto-detected
zone when it's NULL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_v18r7_user_timezone"
down_revision = "0004_v18r7_archive_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("timezone")
