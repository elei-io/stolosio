from typing import Protocol

from backend.events.contracts import SessionEvent


class EventPublisher(Protocol):
    async def publish(self, event: SessionEvent) -> None: ...


class NullEventPublisher:
    async def publish(self, event: SessionEvent) -> None:
        return None


class DynamicEventPublisher:
    def __init__(self) -> None:
        self._publisher: EventPublisher | None = None

    def replace(self, publisher: EventPublisher | None) -> None:
        self._publisher = publisher

    async def publish(self, event: SessionEvent) -> None:
        publisher = self._publisher
        if publisher is not None:
            await publisher.publish(event)
