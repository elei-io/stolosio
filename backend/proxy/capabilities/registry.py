import hashlib

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
            expression = params.get("expression") if isinstance(params, dict) else None
            return (
                isinstance(expression, str)
                and hashlib.sha256(expression.encode()).hexdigest().startswith(
                    "6947ff8ef8a7"
                )
            )
        if provider is ProviderName.HTTP and method == "Runtime.callFunctionOn":
            declaration = (
                params.get("functionDeclaration") if isinstance(params, dict) else None
            )
            return (
                isinstance(declaration, str)
                and "utilityScript.evaluate" in declaration
                and "outerHTML" in str(params)
            )
        if method == "Emulation.setScriptExecutionDisabled":
            value = params.get("value") if isinstance(params, dict) else None
            if not isinstance(value, bool):
                return False
            if provider in {ProviderName.HTTP, ProviderName.CHROMIUM, ProviderName.BROWSERLESS}:
                return True
            return provider is ProviderName.LIGHTPANDA and value is False
        return method in self._manifests.get(provider, frozenset())

    def supports_observed_method(self, provider: ProviderName, method: str) -> bool:
        """Method-only coverage for historical projections without retained params."""
        return method in self._manifests.get(provider, frozenset())


capability_registry = CapabilityRegistry(PROVIDER_METHODS)
