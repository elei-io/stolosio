"""Add deterministic provider routing and qualification."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_04"
down_revision: str | None = "20260717_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gateway_sessions",
        sa.Column("qualification_evaluated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_gateway_sessions_qualification_evaluated_at",
        "gateway_sessions",
        ["qualification_evaluated_at"],
    )
    op.add_column(
        "domains",
        sa.Column(
            "eligible_acquisition_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column("acquisition_attempts", sa.Column("domain_id", sa.BigInteger()))
    op.add_column("acquisition_attempts", sa.Column("routing_reason", sa.String(32)))
    op.add_column("acquisition_attempts", sa.Column("routing_version", sa.Integer()))
    op.add_column("acquisition_attempts", sa.Column("estimated_cost_units", sa.BigInteger()))
    op.add_column("acquisition_attempts", sa.Column("actual_cost_units", sa.BigInteger()))
    op.add_column(
        "acquisition_attempts",
        sa.Column("cost_projected", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_foreign_key(
        "fk_acquisition_attempts_domain",
        "acquisition_attempts",
        "domains",
        ["domain_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_acquisition_attempts_domain_id",
        "acquisition_attempts",
        ["domain_id"],
    )

    op.create_table(
        "routing_configuration",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("default_provider", sa.String(32), nullable=False),
        sa.Column(
            "existing_domain_probe_rate_basis_points",
            sa.Integer(),
            server_default="100",
            nullable=False,
        ),
        sa.Column(
            "required_successful_probes",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "comparison_policy_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "configuration_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "existing_domain_probe_rate_basis_points BETWEEN 0 AND 10000",
            name="ck_routing_probe_rate",
        ),
        sa.CheckConstraint(
            "required_successful_probes >= 1",
            name="ck_routing_required_probes",
        ),
    )
    op.create_table(
        "provider_routing_profiles",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column(
            "automatic_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column("cost_units_per_second", sa.BigInteger(), nullable=False),
        sa.Column(
            "capability_manifest_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cost_units_per_second >= 0",
            name="ck_provider_cost_nonnegative",
        ),
    )
    op.create_table(
        "domain_provider_profiles",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("qualification_state", sa.String(16), nullable=False),
        sa.Column(
            "successful_probe_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "failed_probe_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "observed_session_count",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_cost_units",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.Column("comparison_policy_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "qualification_state IN ('unqualified', 'probing', 'qualified', 'rejected')",
            name="ck_domain_provider_qualification_state",
        ),
    )
    op.create_table(
        "qualification_probes",
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
        sa.Column("baseline_status", sa.Integer(), nullable=False),
        sa.Column(
            "baseline_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("baseline_console_errors", sa.Integer(), nullable=False),
        sa.Column("baseline_content_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("candidate_status", sa.Integer()),
        sa.Column(
            "candidate_headers",
            postgresql.JSONB(astext_type=sa.Text()),
        ),
        sa.Column("candidate_console_errors", sa.Integer()),
        sa.Column("candidate_content_fingerprint", sa.String(64)),
        sa.Column("comparison_outcome", sa.String(32)),
        sa.Column("cost_units", sa.BigInteger()),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_session_id", "candidate_provider"),
        sa.CheckConstraint(
            "trigger IN ('new_domain', 'existing_sample')",
            name="ck_qualification_probe_trigger",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed')",
            name="ck_qualification_probe_state",
        ),
    )
    op.create_index(
        "ix_qualification_probes_queue",
        "qualification_probes",
        ["state", "created_at"],
    )
    op.create_index(
        "ux_qualification_probes_initial",
        "qualification_probes",
        ["domain_id", "candidate_provider"],
        unique=True,
        postgresql_where=sa.text("trigger = 'new_domain'"),
    )


def downgrade() -> None:
    op.drop_table("qualification_probes")
    op.drop_table("domain_provider_profiles")
    op.drop_table("provider_routing_profiles")
    op.drop_table("routing_configuration")
    op.drop_index("ix_acquisition_attempts_domain_id", table_name="acquisition_attempts")
    op.drop_constraint(
        "fk_acquisition_attempts_domain",
        "acquisition_attempts",
        type_="foreignkey",
    )
    for column in (
        "cost_projected",
        "actual_cost_units",
        "estimated_cost_units",
        "routing_version",
        "routing_reason",
        "domain_id",
    ):
        op.drop_column("acquisition_attempts", column)
    op.drop_column("domains", "eligible_acquisition_count")
    op.drop_index(
        "ix_gateway_sessions_qualification_evaluated_at",
        table_name="gateway_sessions",
    )
    op.drop_column("gateway_sessions", "qualification_evaluated_at")
