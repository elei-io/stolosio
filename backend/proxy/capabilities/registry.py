from backend.proxy.capabilities.http import (
    is_content_call,
    is_utility_evaluation,
)
from backend.proxy.capabilities.manifests import PROVIDER_METHODS
from backend.proxy.contracts import ProviderName


class CapabilityRegistry:
    def __init__(self, manifests: dict[ProviderName, frozenset[str]]) -> None:
        self._manifests = manifests

    def supports(
        self,
        provider: ProviderName,
        method: str,
        params: dict | None = None,
    ) -> bool:
        if provider is ProviderName.HTTP and method == "Runtime.evaluate":
            return is_utility_evaluation(params) if isinstance(params, dict) else False
        if provider is ProviderName.HTTP and method == "Runtime.callFunctionOn":
            return is_content_call(params) if isinstance(params, dict) else False
        if method == "Emulation.setScriptExecutionDisabled":
            value = params.get("value") if isinstance(params, dict) else None
            if not isinstance(value, bool):
                return False
            if provider in {ProviderName.HTTP, ProviderName.CHROMIUM, ProviderName.BROWSERLESS}:
                return True
            return provider is ProviderName.LIGHTPANDA and value is False
        return method in self._manifests.get(provider, frozenset())

capability_registry = CapabilityRegistry(PROVIDER_METHODS)
