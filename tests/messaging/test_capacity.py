import asyncio

import pytest

from backend.messaging import NatsCapacityNotifier, PollingNotifier
from backend.proxy.contracts import ProviderName


@pytest.mark.asyncio
async def test_polling_notifier_waits_without_external_state() -> None:
    notifier = PollingNotifier()
    before = asyncio.get_running_loop().time()

    await notifier.wait(ProviderName.CHROMIUM, 0.01)

    assert asyncio.get_running_loop().time() - before >= 0.01


@pytest.mark.asyncio
async def test_nats_capacity_notification_wakes_waiter() -> None:
    try:
        notifier = await NatsCapacityNotifier.connect(
            "nats://localhost:4222",
            connect_timeout_seconds=0.5,
        )
    except Exception:
        pytest.skip("NATS integration service is not available")

    try:
        waiter = asyncio.create_task(notifier.wait(ProviderName.CHROMIUM, 1))
        await asyncio.sleep(0.01)
        await notifier.notify(ProviderName.CHROMIUM)
        await asyncio.wait_for(waiter, timeout=0.5)
    finally:
        await notifier.close()
