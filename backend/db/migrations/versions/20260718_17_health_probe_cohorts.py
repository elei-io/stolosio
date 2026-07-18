"""Group health probes into comparable provider cohorts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_17"
down_revision: str | None = "20260718_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("health_probes", sa.Column("cohort_id", sa.String(36)))
    op.add_column(
        "health_probes",
        sa.Column(
            "comparison_state",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.execute("UPDATE health_probes SET cohort_id = id")
    op.alter_column("health_probes", "cohort_id", nullable=False)
    op.create_index(
        "ix_health_probes_cohort",
        "health_probes",
        ["cohort_id", "state"],
    )
    op.create_check_constraint(
        "ck_health_probe_comparison_state",
        "health_probes",
        "comparison_state IN "
        "('pending', 'not_applicable', 'inconclusive', "
        "'comparable', 'materially_incomplete')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_health_probe_comparison_state",
        "health_probes",
        type_="check",
    )
    op.drop_index("ix_health_probes_cohort", table_name="health_probes")
    op.drop_column("health_probes", "comparison_state")
    op.drop_column("health_probes", "cohort_id")
