from backend.proxy.adapters.cdp import DirectCdpAdapter, DiscoveredCdpAdapter
from backend.proxy.contracts import ProviderAdapter, ProviderName
from backend.settings import settings


def get_provider_adapter(provider: ProviderName) -> ProviderAdapter:
    match provider:
        case ProviderName.CHROMIUM:
            return DiscoveredCdpAdapter(provider, str(settings.chromium_url))
        case ProviderName.BROWSERLESS:
            return DirectCdpAdapter(provider, str(settings.browserless_url))
        case ProviderName.LIGHTPANDA:
            return DirectCdpAdapter(provider, str(settings.lightpanda_url))
        case ProviderName.CAMOUFOX:
            raise NotImplementedError("Camoufox requires CDP protocol mapping")
