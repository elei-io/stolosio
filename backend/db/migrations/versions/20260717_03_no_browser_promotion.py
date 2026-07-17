"""Add factual per-domain no-browser promotion history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_03"
down_revision: str | None = "20260716_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_promotion_stats",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("promotion_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_trigger_method", sa.String(128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("domain_promotion_stats")
