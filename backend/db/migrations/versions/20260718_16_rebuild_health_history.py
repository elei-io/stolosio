"""Rebuild retained probe history from health facts only."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_16"
down_revision: str | None = "20260718_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE health_probes
        SET
            outcome = CASE
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
                ELSE 'inconclusive'
            END,
            reason_codes = reason_codes
                - 'missing_declared_methods'
                - 'unsupported_observed_methods'
                - 'observed_command_failed'
                - 'probe_command_failed'
        WHERE state IN ('completed', 'failed')
        """
    )
    op.execute(
        """
        UPDATE domain_provider_health AS health
        SET
            successful_probe_count = counts.healthy_count,
            failed_probe_count = counts.unhealthy_count,
            inconclusive_probe_count = counts.inconclusive_count
        FROM (
            SELECT
                domain_id,
                candidate_provider AS provider,
                count(*) FILTER (WHERE outcome = 'healthy') AS healthy_count,
                count(*) FILTER (WHERE outcome = 'unhealthy') AS unhealthy_count,
                count(*) FILTER (WHERE outcome = 'inconclusive') AS inconclusive_count
            FROM health_probes
            WHERE state IN ('completed', 'failed')
            GROUP BY domain_id, candidate_provider
        ) AS counts
        WHERE health.domain_id = counts.domain_id
          AND health.provider = counts.provider
        """
    )
    op.execute(
        """
        UPDATE domain_provider_health
        SET
            failure_reason_code = NULL,
            last_healthy_at = COALESCE(last_healthy_at, last_checked_at)
        WHERE health_state = 'healthy'
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The rebuilt health history is irreversible")
