from backend.proxy.capabilities import CapabilityRegistry, capability_registry
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSession,
    ResolvedSessionSettings,
)
from backend.proxy.provider_transition.session import HttpCdpSession
from backend.settings import Settings, settings


class HttpAdapter:
    provider = ProviderName.HTTP

    def __init__(
        self,
        *,
        capabilities: CapabilityRegistry = capability_registry,
        runtime_settings: Settings = settings,
    ) -> None:
        self._capabilities = capabilities
        self._settings = runtime_settings

    async def acquire(
        self,
        session: HarborSession,
        resolved: ResolvedSessionSettings,
    ) -> ProviderSession:
        return HttpCdpSession(
            session,
            resolved,
            self._capabilities,
            self._settings,
        )
