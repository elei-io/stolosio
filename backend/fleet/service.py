from collections.abc import Mapping
from typing import Any

from backend.fleet.contracts import FleetConfiguration, FleetSnapshot
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
        return await self._repository.update_configuration(provider, values, actor=actor)

    async def snapshot(self, provider: ProviderName) -> FleetSnapshot | None:
        return await self._repository.snapshot(provider)
