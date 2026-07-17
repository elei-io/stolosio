"""Remove stale provider-transition names from database constraints."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_10"
down_revision: str | None = "20260717_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename_constraint(table: str, old: str, new: str) -> None:
    op.execute(
        f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"'
    )


def upgrade() -> None:
    transition_constraints = {
        "domain_escalation_stats_domain_id_fkey": (
            "domain_provider_transition_stats_domain_id_fkey"
        ),
        "domain_escalation_stats_domain_id_not_null": (
            "domain_provider_transition_stats_domain_id_not_null"
        ),
        "domain_escalation_stats_escalation_count_not_null": (
            "domain_provider_transition_stats_transition_count_not_null"
        ),
        "domain_escalation_stats_first_seen_at_not_null": (
            "domain_provider_transition_stats_first_seen_at_not_null"
        ),
        "domain_escalation_stats_from_provider_not_null": (
            "domain_provider_transition_stats_from_provider_not_null"
        ),
        "domain_escalation_stats_last_seen_at_not_null": (
            "domain_provider_transition_stats_last_seen_at_not_null"
        ),
        "domain_escalation_stats_last_trigger_method_not_null": (
            "domain_provider_transition_stats_last_trigger_method_not_null"
        ),
        "domain_escalation_stats_pkey": "domain_provider_transition_stats_pkey",
        "domain_escalation_stats_to_provider_not_null": (
            "domain_provider_transition_stats_to_provider_not_null"
        ),
        "domain_escalation_stats_trigger_not_null": (
            "domain_provider_transition_stats_trigger_not_null"
        ),
    }
    for old, new in transition_constraints.items():
        _rename_constraint("domain_provider_transition_stats", old, new)

    _rename_constraint(
        "domain_provider_support",
        "domain_provider_support_methods_state_not_null",
        "domain_provider_support_method_coverage_state_not_null",
    )
    _rename_constraint(
        "domain_provider_support",
        "domain_provider_support_method_supported_count_not_null",
        "domain_provider_support_method_declared_count_not_null",
    )
    _rename_constraint(
        "support_probes",
        "support_probes_method_supported_count_not_null",
        "support_probes_method_declared_count_not_null",
    )


def downgrade() -> None:
    raise NotImplementedError("The provider-transition constraint cutover is irreversible")
