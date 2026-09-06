from collections.abc import AsyncIterator
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.proxy.contracts.session import StolosioSession
    from backend.proxy.contracts.settings import ResolvedSessionSettings


class ProviderName(StrEnum):
    HTTP = "http"
    BROWSERLESS = "browserless"
    BROWSERBASE = "browserbase"


ACTIVE_PROVIDERS = (
    ProviderName.HTTP,
    ProviderName.BROWSERLESS,
    ProviderName.BROWSERBASE,
)

# Browserbase is never probed automatically. Operators may explicitly request a
# manual Browserbase probe when they accept the associated cost.
PROMOTION_PROVIDERS = (
    ProviderName.HTTP,
    ProviderName.BROWSERLESS,
)


class ProviderSession(Protocol):
    provider: ProviderName | None
    disconnect_reason: str | None

    async def send(self, message: str) -> None: ...
    def messages(self) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...


class ProviderAdapter(Protocol):
    provider: ProviderName

    async def acquire(
        self,
        session: "StolosioSession",
        settings: "ResolvedSessionSettings",
    ) -> ProviderSession: ...
