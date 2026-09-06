from uuid import UUID

import pytest

from backend.proxy.contracts import (
    ProviderName,
    ProviderSelection,
    SettingSource,
    SettingsResolutionContext,
)
from backend.proxy.errors import InvalidStolosioSettings
from backend.proxy.settings import (
    StolosioSettingsResolver,
    stolosio_settings_registry,
    stolosio_settings_resolver,
)


@pytest.mark.asyncio
async def test_omitted_provider_stays_unresolved_until_automatic_selection() -> None:
    requested, resolved = await stolosio_settings_resolver.resolve([])

    assert requested.provider is ProviderSelection.AUTO
    assert resolved.provider.slug is None
    assert resolved.sources["stolosio.provider.slug"] is SettingSource.AUTO


@pytest.mark.asyncio
async def test_resolves_explicit_provider_and_ignores_non_stolosio_keys() -> None:
    requested, resolved = await stolosio_settings_resolver.resolve(
        [("client.setting", "value"), ("stolosio.provider.slug", "browserbase")]
    )

    assert requested.provider is ProviderSelection.BROWSERBASE
    assert resolved.provider.slug is ProviderName.BROWSERBASE
    assert resolved.sources["stolosio.provider.slug"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
async def test_explicit_auto_uses_the_planner() -> None:
    requested, resolved = await stolosio_settings_resolver.resolve(
        [("stolosio.provider.slug", "auto")]
    )

    assert requested.provider is ProviderSelection.AUTO
    assert requested.auto_fields == frozenset({"stolosio.provider.slug"})
    assert resolved.provider.slug is None
    assert resolved.sources["stolosio.provider.slug"] is SettingSource.AUTO


def test_registry_exposes_the_canonical_query_contract() -> None:
    assert stolosio_settings_registry.queries == frozenset(
        {
            "stolosio.provider.slug",
            "stolosio.provider.allow_paid_fallback",
            "stolosio.session.reference",
        }
    )


@pytest.mark.asyncio
async def test_paid_fallback_requires_an_explicit_session_opt_in() -> None:
    _, defaulted = await stolosio_settings_resolver.resolve([])
    requested, allowed = await stolosio_settings_resolver.resolve(
        [("stolosio.provider.allow_paid_fallback", "true")]
    )

    assert defaulted.provider.allow_paid_fallback is False
    assert requested.overrides["stolosio.provider.allow_paid_fallback"] is True
    assert allowed.provider.allow_paid_fallback is True
    assert allowed.sources["stolosio.provider.allow_paid_fallback"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
async def test_resolves_client_generated_session_reference() -> None:
    requested, resolved = await stolosio_settings_resolver.resolve(
        [("stolosio.session.reference", "3a10fc5f-1b31-45d4-b95f-3981ea833a91")]
    )

    assert requested.session_reference == UUID("3a10fc5f-1b31-45d4-b95f-3981ea833a91")
    assert resolved.session.reference == requested.session_reference
    assert resolved.sources["stolosio.session.reference"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
async def test_resolver_uses_one_context_aware_planner_for_automatic_fields() -> None:
    seen: list[SettingsResolutionContext] = []

    class Planner:
        async def plan(self, context, requested, defaults):
            seen.append(context)
            return {"stolosio.provider.slug": ProviderName.BROWSERBASE}

    resolver = StolosioSettingsResolver(stolosio_settings_registry, Planner())
    context = SettingsResolutionContext(target_url="https://example.com")

    _, resolved = await resolver.resolve([], context)

    assert seen == [context]
    assert resolved.provider.slug is ProviderName.BROWSERBASE
    assert resolved.sources["stolosio.provider.slug"] is SettingSource.AUTO


@pytest.mark.asyncio
async def test_explicit_value_takes_precedence_over_the_automatic_plan() -> None:
    class Planner:
        async def plan(self, context, requested, defaults):
            return {"stolosio.provider.slug": ProviderName.BROWSERBASE}

    resolver = StolosioSettingsResolver(stolosio_settings_registry, Planner())

    _, resolved = await resolver.resolve([("stolosio.provider.slug", "browserless")])

    assert resolved.provider.slug is ProviderName.BROWSERLESS
    assert resolved.sources["stolosio.provider.slug"] is SettingSource.EXPLICIT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        [("stolosio.provider.slug", "")],
        [("stolosio.provider.slug", "BROWSERLESS")],
        [("stolosio.provider.slug", "unknown")],
        [("stolosio.provider.slug", "browser")],
        [("stolosio.provider.slug", "premium")],
        [("stolosio.provider.slug", "none")],
        [("stolosio.unknown", "value")],
        [("stolosio.provider", "browserless")],
        [("stolosio.provider.slug", "auto"), ("stolosio.provider.slug", "auto")],
        [("stolosio.session.reference", "not-a-uuid")],
        [("stolosio.session.reference", "auto")],
    ],
)
async def test_rejects_invalid_stolosio_settings(query: list[tuple[str, str]]) -> None:
    with pytest.raises(InvalidStolosioSettings):
        await stolosio_settings_resolver.resolve(query)
