"""Normalize retained session facts to provider-support vocabulary."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_08"
down_revision: str | None = "20260717_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE acquisition_attempts
        SET selection_reason = CASE selection_reason
            WHEN 'unknown_domain_default' THEN 'unknown_domain_bootstrap'
            WHEN 'unqualified_domain_default' THEN 'unknown_domain_bootstrap'
            WHEN 'cheapest_qualified' THEN 'cheapest_supported'
            WHEN 'qualification_probe' THEN 'explicit'
            WHEN 'promotion' THEN 'runtime_escalation'
            ELSE selection_reason
        END
        """
    )
    op.execute(
        """
        UPDATE acquisition_attempts
        SET terminal_reason = 'escalated'
        WHERE terminal_reason = 'promoted'
        """
    )
    op.execute(
        """
        UPDATE session_events
        SET event_type = 'execution.escalated'
        WHERE event_type = 'execution.promoted'
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The greenfield support vocabulary cutover is irreversible")
