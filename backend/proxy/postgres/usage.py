from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AcquisitionAttempt, ProviderRoutingProfile
from backend.proxy.contracts import ProviderName


async def finalize_attempt_usage(
    database: AsyncSession,
    row: AcquisitionAttempt,
    now: datetime,
) -> None:
    if row.provider_started_at is not None and row.provider_ended_at is None:
        row.provider_ended_at = now
        row.provider_reported_ms = max(
            0,
            round((now - row.provider_started_at).total_seconds() * 1000),
        )
    if row.acquiring_at is not None:
        row.capacity_occupied_ms = max(
            0,
            round((now - row.acquiring_at).total_seconds() * 1000),
        )
    if row.provider != ProviderName.HTTP.value and row.active_at is not None:
        row.browser_connected_ms = max(
            0,
            round((now - row.active_at).total_seconds() * 1000),
        )
    if row.provider == ProviderName.BROWSERBASE.value:
        measured = row.provider_reported_ms or row.browser_connected_ms
        if measured is not None:
            row.estimated_billable_ms = max(60_000, measured)

    if row.provider == ProviderName.BROWSERLESS.value:
        row.chargeable_time_ms = row.capacity_occupied_ms
        row.cost_basis = "capacity_occupied"
    elif row.provider == ProviderName.BROWSERBASE.value:
        row.chargeable_time_ms = row.estimated_billable_ms
        row.cost_basis = "estimated_billable"
    else:
        start = row.active_at or row.acquiring_at
        row.chargeable_time_ms = (
            max(0, round((now - start).total_seconds() * 1000))
            if start is not None
            else None
        )
        row.cost_basis = "execution"

    profile = await database.get(ProviderRoutingProfile, row.provider)
    if profile is not None and row.chargeable_time_ms is not None:
        row.cost_rate_units_per_second = profile.cost_units_per_second
        row.modeled_cost_units = (
            row.chargeable_time_ms * profile.cost_units_per_second + 999
        ) // 1000
