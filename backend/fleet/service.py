from collections.abc import Mapping
from typing import Any

from backend.fleet.contracts import FleetConfiguration, FleetSnapshot
from backend.fleet.providers import managed_fleet_definition
from backend.fleet.repository import FleetRepository
from backend.proxy.contracts import ProviderName


class FleetService:
    def __init__(self, repository: FleetRepository) -> None:
        self._repository = repository

    async def configurations(self) -> list[FleetConfiguration]:
        return await self._repository.list_configurations()

    async def configuration(self, provider: ProviderName) -> FleetConfiguration | None:
        return await self._repository.configuration(provider)

    async def update(
        self,
        provider: ProviderName,
        values: Mapping[str, Any],
        *,
        actor: str,
    ) -> FleetConfiguration | None:
        definition = managed_fleet_definition(provider)
        capacity = values.get("session_capacity_per_instance")
        if (
            definition is not None
            and definition.maximum_session_capacity is not None
            and capacity is not None
            and capacity > definition.maximum_session_capacity
        ):
            raise ValueError(
                f"{provider.value} supports at most "
                f"{definition.maximum_session_capacity} session per instance"
            )
        return await self._repository.update_configuration(provider, values, actor=actor)

    async def snapshot(self, provider: ProviderName) -> FleetSnapshot | None:
        return await self._repository.snapshot(provider)
