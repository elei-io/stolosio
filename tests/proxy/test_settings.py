from uuid import UUID

import pytest

from backend.proxy.contracts import (
    ProviderName,
    ProviderSelection,
    SettingSource,
    SettingsResolutionContext,
)
from backend.proxy.errors import InvalidHarborSettings
from backend.proxy.settings import (
    HarborSettingsResolver,
    harbor_settings_registry,
    harbor_settings_resolver,
)


@pytest.mark.asyncio
async def test_omitted_provider_is_automatically_resolved_from_schema_default() -> None:
    requested, resolved = await harbor_settings_resolver.resolve([])

    assert requested.provider is ProviderSelection.AUTO
    assert resolved.provider.slug is ProviderName.CHROMIUM
    assert resolved.sources["harbor.provider.slug"] is SettingSource.AUTO


@pytest.mark.asyncio
async def test_resolves_explicit_provider_and_ignores_non_harbor_keys() -> None:
    requested, resolved = await harbor_settings_resolver.resolve(
        [("client.setting", "value"), ("harbor.provider.slug", "lightpanda")]
    )

    assert requested.provider is ProviderSelection.LIGHTPANDA
    assert resolved.provider.slug is ProviderName.LIGHTPANDA
    assert resolved.sources["harbor.provider.slug"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
async def test_explicit_auto_uses_the_planner() -> None:
    requested, resolved = await harbor_settings_resolver.resolve([("harbor.provider.slug", "auto")])

    assert requested.provider is ProviderSelection.AUTO
    assert requested.auto_fields == frozenset({"harbor.provider.slug"})
    assert resolved.provider.slug is ProviderName.CHROMIUM
    assert resolved.sources["harbor.provider.slug"] is SettingSource.AUTO


def test_registry_exposes_the_canonical_query_contract() -> None:
    assert harbor_settings_registry.queries == frozenset(
        {"harbor.provider.slug", "harbor.session.reference"}
    )


@pytest.mark.asyncio
async def test_resolves_client_generated_session_reference() -> None:
    requested, resolved = await harbor_settings_resolver.resolve(
        [("harbor.session.reference", "3a10fc5f-1b31-45d4-b95f-3981ea833a91")]
    )

    assert requested.session_reference == UUID("3a10fc5f-1b31-45d4-b95f-3981ea833a91")
    assert resolved.session.reference == requested.session_reference
    assert resolved.sources["harbor.session.reference"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
async def test_resolver_uses_one_context_aware_planner_for_automatic_fields() -> None:
    seen: list[SettingsResolutionContext] = []

    class Planner:
        async def plan(self, context, requested, defaults):
            seen.append(context)
            return {"harbor.provider.slug": ProviderName.LIGHTPANDA}

    resolver = HarborSettingsResolver(harbor_settings_registry, Planner())
    context = SettingsResolutionContext(target_url="https://example.com")

    _, resolved = await resolver.resolve([], context)

    assert seen == [context]
    assert resolved.provider.slug is ProviderName.LIGHTPANDA
    assert resolved.sources["harbor.provider.slug"] is SettingSource.AUTO


@pytest.mark.asyncio
async def test_explicit_value_takes_precedence_over_the_automatic_plan() -> None:
    class Planner:
        async def plan(self, context, requested, defaults):
            return {"harbor.provider.slug": ProviderName.LIGHTPANDA}

    resolver = HarborSettingsResolver(harbor_settings_registry, Planner())

    _, resolved = await resolver.resolve([("harbor.provider.slug", "browserless")])

    assert resolved.provider.slug is ProviderName.BROWSERLESS
    assert resolved.sources["harbor.provider.slug"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        [("harbor.provider.slug", "")],
        [("harbor.provider.slug", "CHROMIUM")],
        [("harbor.provider.slug", "unknown")],
        [("harbor.unknown", "value")],
        [("harbor.provider", "chromium")],
        [("harbor.provider.slug", "auto"), ("harbor.provider.slug", "auto")],
        [("harbor.session.reference", "not-a-uuid")],
        [("harbor.session.reference", "auto")],
    ],
)
async def test_rejects_invalid_harbor_settings(query: list[tuple[str, str]]) -> None:
    with pytest.raises(InvalidHarborSettings):
        await harbor_settings_resolver.resolve(query)
