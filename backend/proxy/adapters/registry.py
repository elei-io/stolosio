from backend.proxy.adapters.camoufox import CamoufoxAdapter
from backend.proxy.adapters.cdp import DirectCdpAdapter, DiscoveredCdpAdapter
from backend.proxy.adapters.lightpanda import LightpandaAdapter
from backend.proxy.contracts import ProviderAdapter, ProviderName
from backend.settings import settings


def get_provider_adapter(provider: ProviderName, *, endpoint: str | None = None) -> ProviderAdapter:
    match provider:
        case ProviderName.HTTP:
            from backend.proxy.adapters.http import HttpAdapter

            return HttpAdapter()
        case ProviderName.CHROMIUM:
            return DiscoveredCdpAdapter(provider, endpoint or str(settings.chromium_url))
        case ProviderName.BROWSERLESS:
            return DirectCdpAdapter(provider, endpoint or str(settings.browserless_url))
        case ProviderName.LIGHTPANDA:
            return LightpandaAdapter(endpoint or str(settings.lightpanda_url))
        case ProviderName.CAMOUFOX:
            return CamoufoxAdapter(endpoint or str(settings.camoufox_url))
