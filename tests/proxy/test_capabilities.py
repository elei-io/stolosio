import pytest

from backend.proxy.capabilities import capability_registry
from backend.proxy.contracts import ProviderName


@pytest.mark.parametrize(
    ("provider", "method"),
    [
        (ProviderName.CHROMIUM, "Page.navigate"),
        (ProviderName.BROWSERLESS, "Page.navigate"),
        (ProviderName.BROWSERLESS, "Page.setFontFamilies"),
        (ProviderName.LIGHTPANDA, "Page.navigate"),
        (ProviderName.LIGHTPANDA, "Target.closeTarget"),
    ],
)
def test_explicitly_verified_provider_method_is_supported(
    provider: ProviderName,
    method: str,
) -> None:
    assert capability_registry.supports(provider, method)


@pytest.mark.parametrize("provider", list(ProviderName))
def test_unverified_method_is_not_supported(provider: ProviderName) -> None:
    assert not capability_registry.supports(provider, "Page.printToPDF")


def test_camoufox_enables_only_implemented_mapping_baseline() -> None:
    assert capability_registry.supports(ProviderName.CAMOUFOX, "Page.navigate")
    assert not capability_registry.supports(ProviderName.CAMOUFOX, "Page.printToPDF")


def test_script_execution_capability_depends_on_requested_value() -> None:
    disabled = {"value": True}
    enabled = {"value": False}

    assert capability_registry.supports(
        ProviderName.HTTP,
        "Emulation.setScriptExecutionDisabled",
        disabled,
    )
    assert capability_registry.supports(
        ProviderName.CHROMIUM,
        "Emulation.setScriptExecutionDisabled",
        disabled,
    )
    assert not capability_registry.supports(
        ProviderName.LIGHTPANDA,
        "Emulation.setScriptExecutionDisabled",
        disabled,
    )
    assert capability_registry.supports(
        ProviderName.LIGHTPANDA,
        "Emulation.setScriptExecutionDisabled",
        enabled,
    )
