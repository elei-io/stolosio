from typing import Any, Protocol

from backend.proxy.contracts import RequestedSessionSettings, SettingsResolutionContext


class StolosioPlanner(Protocol):
    async def plan(
        self,
        context: SettingsResolutionContext,
        requested: RequestedSessionSettings,
        defaults: dict[str, Any],
    ) -> dict[str, Any]: ...


class StaticStolosioPlanner:
    async def plan(
        self,
        context: SettingsResolutionContext,
        requested: RequestedSessionSettings,
        defaults: dict[str, Any],
    ) -> dict[str, Any]:
        return defaults.copy()


static_stolosio_planner: StolosioPlanner = StaticStolosioPlanner()
