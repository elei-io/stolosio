"""Hard-cut automatic defaults, method evidence, and provider-transition vocabulary."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_09"
down_revision: str | None = "20260717_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "routing_configuration",
        "unknown_domain_provider",
        new_column_name="default_provider",
    )
    op.alter_column(
        "acquisition_attempts",
        "escalation_trigger",
        new_column_name="transition_trigger",
    )
    op.rename_table(
        "domain_escalation_stats",
        "domain_provider_transition_stats",
    )
    op.alter_column(
        "domain_provider_transition_stats",
        "escalation_count",
        new_column_name="transition_count",
    )
    for table in ("domain_provider_support", "support_probes"):
        op.alter_column(
            table,
            "methods_state",
            new_column_name="method_coverage_state",
        )
        op.alter_column(
            table,
            "method_supported_count",
            new_column_name="method_declared_count",
        )
    op.execute(
        """
        UPDATE domain_provider_support
        SET method_coverage_state = CASE method_coverage_state
            WHEN 'healthy' THEN 'declared'
            WHEN 'unhealthy' THEN 'missing'
            ELSE method_coverage_state
        END
        """
    )
    op.execute(
        """
        UPDATE support_probes
        SET method_coverage_state = CASE method_coverage_state
            WHEN 'healthy' THEN 'declared'
            WHEN 'unhealthy' THEN 'missing'
            ELSE method_coverage_state
        END
        """
    )
    op.execute(
        """
        UPDATE acquisition_attempts
        SET selection_reason = 'configured_default'
        WHERE selection_reason = 'unknown_domain_bootstrap'
        """
    )
    op.execute(
        """
        UPDATE session_events
        SET event_type = 'execution.transitioned'
        WHERE event_type = 'execution.escalated'
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The provider-transition cutover is irreversible")
