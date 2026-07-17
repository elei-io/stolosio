"""Replace baseline qualification with independent provider support evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_07"
down_revision: str | None = "20260717_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Harbor is greenfield: make a hard contract cut instead of retaining two
    # competing sources of routing truth.
    op.drop_table("qualification_probes")
    op.drop_table("domain_provider_profiles")
    op.drop_table("domain_promotion_stats")

    op.alter_column(
        "routing_configuration",
        "default_provider",
        new_column_name="unknown_domain_provider",
    )
    op.alter_column(
        "routing_configuration",
        "required_successful_probes",
        new_column_name="required_support_confirmations",
    )
    op.alter_column(
        "routing_configuration",
        "comparison_policy_version",
        new_column_name="support_policy_version",
    )
    op.drop_constraint("ck_routing_required_probes", "routing_configuration", type_="check")
    op.create_check_constraint(
        "ck_routing_required_support_confirmations",
        "routing_configuration",
        "required_support_confirmations >= 1",
    )

    op.alter_column(
        "gateway_sessions",
        "qualification_evaluated_at",
        new_column_name="support_evaluated_at",
    )
    op.drop_index(
        "ix_gateway_sessions_qualification_evaluated_at",
        table_name="gateway_sessions",
    )
    op.create_index(
        "ix_gateway_sessions_support_evaluated_at",
        "gateway_sessions",
        ["support_evaluated_at"],
    )
    op.alter_column(
        "acquisition_attempts", "routing_reason", new_column_name="selection_reason"
    )
    op.alter_column(
        "acquisition_attempts", "routing_version", new_column_name="plan_version"
    )
    op.add_column("acquisition_attempts", sa.Column("plan_position", sa.Integer()))
    op.add_column(
        "acquisition_attempts", sa.Column("escalation_trigger", sa.String(64))
    )

    op.create_table(
        "domain_provider_support",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("support_state", sa.String(16), nullable=False),
        sa.Column("successful_probe_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("failed_probe_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("inconclusive_probe_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("observed_session_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("total_cost_units", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("navigation_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("status_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("headers_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("methods_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("content_state", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("method_observed_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("method_supported_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("unsupported_methods", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("failure_reason_code", sa.String(64)),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_supported_at", sa.DateTime(timezone=True)),
        sa.Column("support_policy_version", sa.Integer(), nullable=False),
        sa.Column("capability_manifest_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "support_state IN ('unknown', 'checking', 'supported', 'unsupported')",
            name="ck_domain_provider_support_state",
        ),
    )
    op.create_table(
        "support_probes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_provider", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("sampling_bucket", sa.Integer()),
        sa.Column("target_url", sa.String(2048), nullable=False),
        sa.Column("required_methods", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16)),
        sa.Column("navigation_state", sa.String(16)),
        sa.Column("status_state", sa.String(16)),
        sa.Column("headers_state", sa.String(16)),
        sa.Column("methods_state", sa.String(16)),
        sa.Column("content_state", sa.String(16)),
        sa.Column("status_code", sa.Integer()),
        sa.Column("reason_codes", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("method_observed_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("method_supported_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("unsupported_methods", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("content_facts", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("cost_units", sa.BigInteger()),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_session_id", "candidate_provider"),
        sa.CheckConstraint(
            "trigger IN ('new_domain', 'existing_sample')", name="ck_support_probe_trigger"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed')",
            name="ck_support_probe_state",
        ),
    )
    op.create_index("ix_support_probes_queue", "support_probes", ["state", "created_at"])
    op.create_index(
        "ux_support_probes_initial",
        "support_probes",
        ["domain_id", "candidate_provider"],
        unique=True,
        postgresql_where=sa.text("trigger = 'new_domain'"),
    )
    op.create_table(
        "domain_escalation_stats",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("from_provider", sa.String(32), primary_key=True),
        sa.Column("to_provider", sa.String(32), primary_key=True),
        sa.Column("trigger", sa.String(32), primary_key=True),
        sa.Column("escalation_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_trigger_method", sa.String(128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise NotImplementedError("The greenfield provider-support cutover is irreversible")
