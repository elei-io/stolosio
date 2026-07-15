from dataclasses import dataclass
from enum import StrEnum

from backend.proxy.contracts.provider import ProviderName
from backend.proxy.contracts.settings import ProviderSelection


class SessionState(StrEnum):
    REQUESTED = "requested"
    QUEUED = "queued"
    ACQUIRING = "acquiring"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HarborSession:
    session_id: str
    owner_id: str
    lease_token: str
    requested_provider: ProviderSelection
    resolved_provider: ProviderName
    state: SessionState
