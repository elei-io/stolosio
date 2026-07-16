from dataclasses import dataclass
from enum import StrEnum

from backend.proxy.contracts.provider import ProviderName


class SessionState(StrEnum):
    REQUESTED = "requested"
    ADMITTED = "admitted"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class AttemptState(StrEnum):
    REQUESTED = "requested"
    QUEUED = "queued"
    ACQUIRING = "acquiring"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HarborSession:
    session_id: str
    owner_id: str
    lease_token: str
    state: SessionState


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    attempt_id: str
    session_id: str
    ordinal: int
    provider: ProviderName
    state: AttemptState
