from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Protocol

import nats
from nats.aio.client import Client as NatsClient

from backend.messaging.connection import nats_auth_options
from backend.proxy.contracts import ProviderName

_CAPACITY_SUBJECT = "stolosio.v1.capacity"


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


class DynamicCapacityNotifier:
    def __init__(self) -> None:
        self._notifier: NatsCapacityNotifier | None = None

    async def replace(self, notifier: NatsCapacityNotifier | None) -> None:
        previous = self._notifier
        self._notifier = notifier
        if previous is not None and previous is not notifier:
            await previous.close()

    async def wait(self, provider: ProviderName, wait_seconds: float) -> None:
        notifier = self._notifier
        if notifier is None:
            await asyncio.sleep(wait_seconds)
            return
        await notifier.wait(provider, wait_seconds)

    async def notify(self, provider: ProviderName) -> None:
        notifier = self._notifier
        if notifier is not None:
            try:
                await notifier.notify(provider)
            except Exception:
                return None

    async def close(self) -> None:
        await self.replace(None)


class NatsCapacityNotifier:
    def __init__(self, client: NatsClient, *, owns_client: bool) -> None:
        self._client = client
        self._owns_client = owns_client
        self._condition = asyncio.Condition()
        self._generations: defaultdict[str, int] = defaultdict(int)
        self._subscription = None

    @classmethod
    async def start(
        cls,
        client: NatsClient,
        *,
        owns_client: bool = False,
    ) -> NatsCapacityNotifier:
        notifier = cls(client, owns_client=owns_client)
        notifier._subscription = await client.subscribe(
            f"{_CAPACITY_SUBJECT}.*", cb=notifier._receive
        )
        await client.flush()
        return notifier

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        connect_timeout_seconds: float,
        seed: str = "",
    ) -> NatsCapacityNotifier:
        async with asyncio.timeout(connect_timeout_seconds):
            client = await nats.connect(
                servers=[url],
                connect_timeout=connect_timeout_seconds,
                max_reconnect_attempts=-1,
                **nats_auth_options(seed),
            )
        notifier = await cls.start(client, owns_client=True)
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
        if self._subscription is not None:
            await self._subscription.unsubscribe()
        if not self._owns_client:
            return
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
