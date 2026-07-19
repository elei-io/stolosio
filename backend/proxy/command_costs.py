from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import ProviderCommandCostStat
from backend.proxy.contracts import ProviderName


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CommandCostQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def command_costs(
        self,
        *,
        provider: ProviderName | None = None,
        include_overhead: bool = True,
        sort_by: str = "cost",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        query = select(ProviderCommandCostStat)
        if provider is not None:
            query = query.where(ProviderCommandCostStat.provider == provider.value)
        if not include_overhead:
            query = query.where(ProviderCommandCostStat.method != "__session_overhead__")
        ordering = {
            "cost": ProviderCommandCostStat.attributed_cost_units,
            "browser_time": ProviderCommandCostStat.attributed_browser_time_ms,
            "commands": ProviderCommandCostStat.command_count,
        }[sort_by]
        query = query.order_by(
            ordering.desc(),
            ProviderCommandCostStat.attributed_cost_units.desc(),
            ProviderCommandCostStat.provider,
            ProviderCommandCostStat.method,
        ).limit(limit)
        async with self._sessions() as database:
            rows = list(await database.scalars(query))
        return [
            {
                "provider": row.provider,
                "method": row.method,
                "command_count": row.command_count,
                "failed_count": row.failed_count,
                "interrupted_count": row.interrupted_count,
                "total_duration_ms": row.total_duration_ms,
                "total_provider_latency_ms": row.total_provider_latency_ms,
                "total_harbor_queue_ms": row.total_harbor_queue_ms,
                "attributed_browser_time_ms": (row.attributed_browser_time_ms),
                "attributed_cost_units": row.attributed_cost_units,
                "first_seen_at": _iso(row.first_seen_at),
                "last_seen_at": _iso(row.last_seen_at),
            }
            for row in rows
        ]
