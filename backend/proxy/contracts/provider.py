from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ProviderName(StrEnum):
    CHROMIUM = "chromium"
    BROWSERLESS = "browserless"
    LIGHTPANDA = "lightpanda"
    CAMOUFOX = "camoufox"


@dataclass(frozen=True, slots=True)
class ProviderConnection:
    provider: ProviderName
    websocket_url: str
    transport_host: str | None = None
    transport_port: int | None = None


class ProviderAdapter(Protocol):
    async def connect(self) -> ProviderConnection: ...
