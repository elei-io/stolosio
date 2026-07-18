"""Separate provider health from runtime command compatibility."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_14"
down_revision: str | None = "20260717_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_routing_required_support_confirmations",
        "routing_configuration",
        type_="check",
    )
    op.alter_column(
        "routing_configuration",
        "required_support_confirmations",
        new_column_name="required_health_confirmations",
    )
    op.alter_column(
        "routing_configuration",
        "support_policy_version",
        new_column_name="health_policy_version",
    )
    op.create_check_constraint(
        "ck_routing_required_health_confirmations",
        "routing_configuration",
        "required_health_confirmations >= 1",
    )
    op.alter_column(
        "provider_routing_profiles",
        "capability_manifest_version",
        new_column_name="provider_contract_version",
    )

    op.rename_table("domain_provider_support", "domain_provider_health")
    op.drop_constraint(
        "ck_domain_provider_support_state",
        "domain_provider_health",
        type_="check",
    )
    op.alter_column(
        "domain_provider_health",
        "support_state",
        new_column_name="health_state",
    )
    op.alter_column(
        "domain_provider_health",
        "last_supported_at",
        new_column_name="last_healthy_at",
    )
    op.alter_column(
        "domain_provider_health",
        "support_policy_version",
        new_column_name="health_policy_version",
    )
    op.alter_column(
        "domain_provider_health",
        "capability_manifest_version",
        new_column_name="provider_contract_version",
    )

    op.create_table(
        "domain_provider_cost_stats",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column(
            "observed_attempt_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_cost_units",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        INSERT INTO domain_provider_cost_stats (
            domain_id, provider, observed_attempt_count, total_cost_units
        )
        SELECT domain_id, provider, observed_session_count, total_cost_units
        FROM domain_provider_health
        """
    )

    op.create_table(
        "domain_provider_runtime_state",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default="eligible"
        ),
        sa.Column("suppressed_at", sa.DateTime(timezone=True)),
        sa.Column("suppressed_session_id", sa.String(36)),
        sa.Column("incompatible_method", sa.String(128)),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
        sa.Column("restored_session_id", sa.String(36)),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_contract_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('eligible', 'suppressed')",
            name="ck_domain_provider_runtime_state",
        ),
    )
    op.execute(
        """
        INSERT INTO domain_provider_runtime_state (
            domain_id,
            provider,
            state,
            suppressed_at,
            incompatible_method,
            last_evidence_at,
            provider_contract_version
        )
        SELECT
            domain_id,
            provider,
            CASE
                WHEN method_coverage_state = 'missing' THEN 'suppressed'
                ELSE 'eligible'
            END,
            CASE
                WHEN method_coverage_state = 'missing'
                    THEN COALESCE(last_checked_at, now())
                ELSE NULL
            END,
            CASE
                WHEN method_coverage_state = 'missing'
                    THEN unsupported_methods->>0
                ELSE NULL
            END,
            COALESCE(last_checked_at, now()),
            provider_contract_version
        FROM domain_provider_health
        """
    )

    op.create_table(
        "session_domain_provider_compatibility",
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("compatible", sa.Boolean(), nullable=False),
        sa.Column("incompatible_method", sa.String(128)),
        sa.Column(
            "domain_first_seen_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_contract_version", sa.Integer(), nullable=False),
    )

    op.execute(
        """
        UPDATE domain_provider_health
        SET health_state = CASE
            WHEN navigation_state = 'unhealthy'
              OR status_state = 'unhealthy'
              OR headers_state = 'unhealthy'
              OR content_state = 'unhealthy'
                THEN 'unhealthy'
            WHEN navigation_state = 'healthy'
              AND status_state = 'healthy'
              AND headers_state = 'healthy'
              AND content_state = 'healthy'
                THEN 'healthy'
            WHEN navigation_state = 'inconclusive'
              OR status_state = 'inconclusive'
              OR headers_state = 'inconclusive'
              OR content_state = 'inconclusive'
                THEN 'inconclusive'
            WHEN health_state = 'checking' THEN 'checking'
            ELSE 'unknown'
        END
        """
    )
    op.create_check_constraint(
        "ck_domain_provider_health_state",
        "domain_provider_health",
        "health_state IN "
        "('unknown', 'checking', 'healthy', 'unhealthy', 'inconclusive')",
    )
    for column in (
        "observed_session_count",
        "total_cost_units",
        "method_coverage_state",
        "method_observed_count",
        "method_declared_count",
        "unsupported_methods",
    ):
        op.drop_column("domain_provider_health", column)

    op.rename_table("support_probes", "health_probes")
    op.execute(
        "ALTER INDEX ux_support_probes_automatic_source "
        "RENAME TO ux_health_probes_automatic_source"
    )
    op.execute(
        "ALTER INDEX ix_support_probes_queue RENAME TO ix_health_probes_queue"
    )
    op.execute(
        "ALTER INDEX ux_support_probes_initial RENAME TO ux_health_probes_initial"
    )
    op.execute(
        "ALTER TABLE health_probes RENAME CONSTRAINT ck_support_probe_trigger "
        "TO ck_health_probe_trigger"
    )
    op.execute(
        "ALTER TABLE health_probes RENAME CONSTRAINT ck_support_probe_state "
        "TO ck_health_probe_state"
    )
    for column in (
        "required_methods",
        "method_coverage_state",
        "method_observed_count",
        "method_declared_count",
        "unsupported_methods",
    ):
        op.drop_column("health_probes", column)


def downgrade() -> None:
    raise NotImplementedError("The runtime-compatibility cutover is irreversible")
