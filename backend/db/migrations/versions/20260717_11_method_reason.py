"""Rename inferred method-coverage reason codes."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_11"
down_revision: str | None = "20260717_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE support_probes
        SET reason_codes = (
            reason_codes - 'unsupported_observed_methods'
        ) || '["missing_declared_methods"]'::jsonb
        WHERE reason_codes ? 'unsupported_observed_methods'
        """
    )
    op.execute(
        """
        UPDATE domain_provider_support
        SET failure_reason_code = 'missing_declared_methods'
        WHERE failure_reason_code = 'unsupported_observed_methods'
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The method-coverage reason cutover is irreversible")
