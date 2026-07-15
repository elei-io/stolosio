from backend.proxy.capabilities.manifests import PROVIDER_METHODS
from backend.proxy.contracts import ProviderName


class CapabilityRegistry:
    def __init__(self, manifests: dict[ProviderName, frozenset[str]]) -> None:
        self._manifests = manifests

    def supports(self, provider: ProviderName, method: str) -> bool:
        return method in self._manifests.get(provider, frozenset())


capability_registry = CapabilityRegistry(PROVIDER_METHODS)
