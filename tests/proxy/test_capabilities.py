import pytest

from backend.proxy.capabilities import capability_registry
from backend.proxy.capabilities.http import CONTENT_EXPRESSION, UTILITY_CALL
from backend.proxy.contracts import ProviderName


@pytest.mark.parametrize(
    "provider",
    [
        ProviderName.BROWSERLESS,
        ProviderName.BROWSERBASE,
    ],
)
def test_browser_providers_treat_every_method_as_native_passthrough(
    provider: ProviderName,
) -> None:
    assert capability_registry.supports(provider, "Future.methodAddedAfterHarborRelease")


def test_http_remains_an_explicitly_bounded_facade() -> None:
    assert capability_registry.supports(ProviderName.HTTP, "Page.navigate")
    assert not capability_registry.supports(ProviderName.HTTP, "Page.printToPDF")


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


def test_http_script_execution_capability_requires_a_boolean() -> None:
    disabled = {"value": True}
    assert capability_registry.supports(
        ProviderName.HTTP,
        "Emulation.setScriptExecutionDisabled",
        disabled,
    )
    assert not capability_registry.supports(
        ProviderName.HTTP,
        "Emulation.setScriptExecutionDisabled",
        {"value": "true"},
    )
