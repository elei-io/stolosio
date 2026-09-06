from backend.proxy.contracts import (
    ProviderName,
    ProviderSession,
    ResolvedSessionSettings,
    StolosioSession,
)
from backend.proxy.provider_transition.session import HttpCdpSession
from backend.settings import Settings, settings


class HttpAdapter:
    provider = ProviderName.HTTP

    def __init__(
        self,
        *,
        runtime_settings: Settings = settings,
    ) -> None:
        self._settings = runtime_settings

    async def acquire(
        self,
        session: StolosioSession,
        resolved: ResolvedSessionSettings,
    ) -> ProviderSession:
        return HttpCdpSession(
            session,
            resolved,
            self._settings,
        )
