"""v1.9 §21.7: QuestionnaireResponse.extra JSON blob.

Revision ID: 0008_v19_questionnaire_extra
Revises:    0007_v19_capability_list_lifecycle
Create Date: 2026-05-18

The section-by-section progressive UI captures more per-question
state than the original flat catalog flow (target_state_score,
target_state_notes, not_applicable, not_applicable_reason,
current_state_text). We carry those in a JSON blob to avoid a
migration each time the section UI adds a new per-question hint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_v19_q_extra"
down_revision = "0007_v19_cl_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("questionnaire_responses") as batch:
        batch.add_column(sa.Column(
            "extra", sa.JSON,
            nullable=False, server_default=sa.text("'{}'::json"),
        ))


def downgrade() -> None:
    with op.batch_alter_table("questionnaire_responses") as batch:
        batch.drop_column("extra")
