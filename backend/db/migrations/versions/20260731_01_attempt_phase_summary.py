"""add bounded attempt phase summaries

Revision ID: 20260731_01
Revises: 20260719_01
Create Date: 2026-07-31 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_01"
down_revision: str | None = "20260719_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "acquisition_attempts",
        sa.Column(
            "phase_summary",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("acquisition_attempts", "phase_summary")
