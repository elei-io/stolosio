from typing import Any, Protocol

from backend.proxy.contracts import RequestedSessionSettings, SettingsResolutionContext


class HarborPlanner(Protocol):
    async def plan(
        self,
        context: SettingsResolutionContext,
        requested: RequestedSessionSettings,
        defaults: dict[str, Any],
    ) -> dict[str, Any]: ...


class StaticHarborPlanner:
    async def plan(
        self,
        context: SettingsResolutionContext,
        requested: RequestedSessionSettings,
        defaults: dict[str, Any],
    ) -> dict[str, Any]:
        return defaults.copy()


static_harbor_planner: HarborPlanner = StaticHarborPlanner()
