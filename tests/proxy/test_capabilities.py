import pytest

from backend.proxy.capabilities import capability_registry
from backend.proxy.capabilities.http import CONTENT_EXPRESSION, UTILITY_CALL
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


def test_http_method_name_does_not_authorize_arbitrary_evaluation() -> None:
    assert not capability_registry.supports(
        ProviderName.HTTP,
        "Runtime.evaluate",
        {"expression": "document.querySelector('h1').textContent"},
    )


def test_http_content_capability_requires_the_exact_executable_shape() -> None:
    object_id = "harbor-http-utility-test"
    valid = {
        "objectId": object_id,
        "functionDeclaration": UTILITY_CALL,
        "arguments": [
            {"objectId": object_id},
            {"value": True},
            {"value": True},
            {"value": CONTENT_EXPRESSION},
            {"value": 1},
            {"value": {"v": "undefined"}},
        ],
        "returnByValue": True,
        "awaitPromise": True,
    }

    assert capability_registry.supports(
        ProviderName.HTTP, "Runtime.callFunctionOn", valid
    )
    assert not capability_registry.supports(
        ProviderName.HTTP,
        "Runtime.callFunctionOn",
        {**valid, "returnByValue": False},
    )


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
