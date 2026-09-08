from backend.proxy.adapters.browserbase import BrowserbaseAdapter
from backend.proxy.adapters.cdp import DirectCdpAdapter
from backend.proxy.contracts import ProviderAdapter, ProviderName
from backend.proxy.errors import ProviderUnavailable
from backend.settings import settings


def get_provider_adapter(provider: ProviderName, *, endpoint: str | None = None) -> ProviderAdapter:
    match provider:
        case ProviderName.HTTP:
            from backend.proxy.adapters.http import HttpAdapter

            return HttpAdapter()
        case ProviderName.BROWSERLESS:
            return DirectCdpAdapter(
                provider,
                endpoint or str(settings.browserless_url),
                session_timeout_seconds=settings.browserless_session_timeout_seconds,
            )
        case ProviderName.BROWSERBASE:
            if not settings.browserbase_network_isolation_verified:
                raise ProviderUnavailable("Browserbase network isolation has not been verified")
            return BrowserbaseAdapter(
                api_url=str(settings.browserbase_api_url).rstrip("/"),
                api_key=settings.browserbase_api_key,
                project_id=settings.browserbase_project_id,
                timeout_seconds=settings.browserbase_session_timeout_seconds,
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")
