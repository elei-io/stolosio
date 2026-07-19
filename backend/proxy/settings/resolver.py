from backend.proxy.contracts import (
    RequestedSessionSettings,
    ResolvedSessionSettings,
    SettingSource,
    SettingsResolutionContext,
)
from backend.proxy.planner import HarborPlanner, static_harbor_planner
from backend.proxy.settings.registry import HarborSettingsRegistry, harbor_settings_registry


class HarborSettingsResolver:
    def __init__(
        self,
        registry: HarborSettingsRegistry,
        planner: HarborPlanner,
    ) -> None:
        self._registry = registry
        self._planner = planner

    async def resolve(
        self,
        query_items: list[tuple[str, str]],
        context: SettingsResolutionContext | None = None,
    ) -> tuple[RequestedSessionSettings, ResolvedSessionSettings]:
        requested = self._registry.parse(query_items)
        defaults = self._registry.defaults()
        automatic = await self._planner.plan(
            context or SettingsResolutionContext(),
            requested,
            defaults,
        )
        values: dict[str, object] = {}
        sources: dict[str, SettingSource] = {}
        for field in self._registry.fields:
            query = field.query
            if query in requested.overrides:
                values[query] = requested.overrides[query]
                sources[query] = SettingSource.EXPLICIT
            elif field.automatic and query in automatic:
                values[query] = automatic[query]
                sources[query] = SettingSource.AUTO
            else:
                values[query] = defaults[query]
                sources[query] = SettingSource.DEFAULT

        models = self._registry.build_models(values)
        return requested, ResolvedSessionSettings(
            provider=models["provider"],
            session=models["session"],
            sources=sources,
        )


harbor_settings_resolver = HarborSettingsResolver(
    harbor_settings_registry,
    static_harbor_planner,
)
