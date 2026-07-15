from collections.abc import AsyncIterator
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.proxy.contracts.session import HarborSession
    from backend.proxy.contracts.settings import ResolvedSessionSettings


class ProviderName(StrEnum):
    CHROMIUM = "chromium"
    BROWSERLESS = "browserless"
    LIGHTPANDA = "lightpanda"
    CAMOUFOX = "camoufox"


class ProviderSession(Protocol):
    provider: ProviderName

    async def send(self, message: str) -> None: ...
    def messages(self) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...

class ProviderAdapter(Protocol):
    provider: ProviderName

    async def acquire(
        self,
        session: "HarborSession",
        settings: "ResolvedSessionSettings",
    ) -> ProviderSession: ...
