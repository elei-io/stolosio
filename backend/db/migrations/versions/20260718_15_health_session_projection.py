"""Finish the health-vocabulary cutover for session projection."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_15"
down_revision: str | None = "20260718_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_gateway_sessions_support_evaluated_at",
        table_name="gateway_sessions",
    )
    op.alter_column(
        "gateway_sessions",
        "support_evaluated_at",
        new_column_name="health_evaluated_at",
    )
    op.create_index(
        "ix_gateway_sessions_health_evaluated_at",
        "gateway_sessions",
        ["health_evaluated_at"],
    )
    op.execute(
        """
        UPDATE acquisition_attempts
        SET selection_reason = CASE selection_reason
            WHEN 'cheapest_supported' THEN 'cheapest_eligible'
            WHEN 'configured_default' THEN 'configured_default_bootstrap'
            ELSE selection_reason
        END
        WHERE selection_reason IN ('cheapest_supported', 'configured_default')
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The health-session cutover is irreversible")
