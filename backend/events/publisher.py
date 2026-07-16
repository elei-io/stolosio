from typing import Protocol

from backend.events.contracts import SessionEvent


class EventPublisher(Protocol):
    async def publish(self, event: SessionEvent) -> None: ...


class NullEventPublisher:
    async def publish(self, event: SessionEvent) -> None:
        return None
