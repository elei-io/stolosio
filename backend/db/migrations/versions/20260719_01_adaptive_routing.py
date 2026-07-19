"""add network policy and adaptive routing

Revision ID: 20260719_01
Revises: 20260718_01
Create Date: 2026-07-19 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_01"
down_revision: str | None = "20260718_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "network_policy_configuration",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column(
            "blocked_domain_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "configuration_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blocked_domain_patterns) = 'array'",
            name="ck_network_policy_blocked_domains_array",
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "domain_routing_preferences",
        sa.Column("domain_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "preferred_provider",
            sa.String(length=32),
            server_default="http",
            nullable=False,
        ),
        sa.Column(
            "preference_score",
            sa.Float(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "browser_required_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "http_sufficient_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "browser_compatible_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "last_evidence_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preferred_provider IN ('http', 'browserless')",
            name="ck_domain_routing_preference_provider",
        ),
        sa.CheckConstraint(
            "browser_required_count >= 0",
            name="ck_domain_routing_browser_required_nonnegative",
        ),
        sa.CheckConstraint(
            "http_sufficient_count >= 0",
            name="ck_domain_routing_http_sufficient_nonnegative",
        ),
        sa.CheckConstraint(
            "browser_compatible_count >= 0",
            name="ck_domain_routing_browser_compatible_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domains.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("domain_id"),
    )
    op.execute(
        sa.text(
            "UPDATE routing_configuration "
            "SET default_provider = 'browserless', "
            "configuration_version = configuration_version + 1, "
            "updated_at = now() "
            "WHERE default_provider = 'browserbase'"
        )
    )


def downgrade() -> None:
    op.drop_table("domain_routing_preferences")
    op.drop_table("network_policy_configuration")
