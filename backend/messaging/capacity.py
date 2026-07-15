import asyncio
from collections import defaultdict
from typing import Protocol

import nats
from nats.aio.client import Client as NatsClient

from backend.proxy.contracts import ProviderName

_CAPACITY_SUBJECT = "harbor.v1.capacity"


class CapacityNotifier(Protocol):
    async def wait(self, provider: ProviderName, wait_seconds: float) -> None: ...
    async def notify(self, provider: ProviderName) -> None: ...
    async def close(self) -> None: ...


class PollingNotifier:
    async def wait(self, provider: ProviderName, wait_seconds: float) -> None:
        await asyncio.sleep(wait_seconds)

    async def notify(self, provider: ProviderName) -> None:
        return None

    async def close(self) -> None:
        return None


class NatsCapacityNotifier:
    def __init__(self, client: NatsClient) -> None:
        self._client = client
        self._condition = asyncio.Condition()
        self._generations: defaultdict[str, int] = defaultdict(int)

    @classmethod
    async def connect(
        cls, url: str, *, connect_timeout_seconds: float
    ) -> "NatsCapacityNotifier":
        async with asyncio.timeout(connect_timeout_seconds):
            client = await nats.connect(
                servers=[url],
                connect_timeout=connect_timeout_seconds,
                max_reconnect_attempts=-1,
            )
        notifier = cls(client)
        await client.subscribe(f"{_CAPACITY_SUBJECT}.*", cb=notifier._receive)
        await client.flush(timeout=connect_timeout_seconds)
        return notifier

    async def wait(self, provider: ProviderName, wait_seconds: float) -> None:
        async with self._condition:
            generation = self._generations[provider.value]
            try:
                async with asyncio.timeout(wait_seconds):
                    await self._condition.wait_for(
                        lambda: self._generations[provider.value] != generation
                    )
            except TimeoutError:
                pass

    async def notify(self, provider: ProviderName) -> None:
        await self._client.publish(f"{_CAPACITY_SUBJECT}.{provider.value}")

    async def close(self) -> None:
        try:
            async with asyncio.timeout(2):
                await self._client.drain()
        except (TimeoutError, ConnectionError):
            await self._client.close()

    async def _receive(self, message) -> None:
        provider = message.subject.rsplit(".", 1)[-1]
        async with self._condition:
            self._generations[provider] += 1
            self._condition.notify_all()
