"""Show the provider journey selected by Harbor's deterministic automatic plan."""

import asyncio
import os
from uuid import uuid4

from _debug import observe, with_reference
from playwright.async_api import async_playwright

HARBOR_CDP_URL = os.getenv("HARBOR_CDP_URL", "ws://localhost:8411/v1/connect")
HARBOR_DEBUG_URL = os.getenv("HARBOR_DEBUG_URL", "ws://localhost:8411/v1/debug")
TARGET_URL = os.getenv("HARBOR_TARGET_URL", "https://example.net")


async def main() -> None:
    reference = str(uuid4())
    debug = asyncio.create_task(observe(HARBOR_DEBUG_URL, reference))
    await asyncio.sleep(0)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            with_reference(HARBOR_CDP_URL, reference)
        )
        page = await browser.new_page()
        response = await page.goto(TARGET_URL)
        content = await page.content()
        assert response is not None and response.ok and content
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    attempts = [
        event["provider"] for event in events if event["event_type"] == "attempt.started"
    ]
    transitions = [
        event["payload"] for event in events if event["event_type"] == "execution.transitioned"
    ]
    assert attempts
    print({"attempts": attempts, "provider_transitions": transitions})


if __name__ == "__main__":
    asyncio.run(main())
