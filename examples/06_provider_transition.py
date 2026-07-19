"""Prove that an automatic session changes provider for a new requirement."""

import asyncio
import os
from uuid import uuid4

from _debug import observe, with_reference
from playwright.async_api import async_playwright

HARBOR_CDP_URL = os.getenv(
    "HARBOR_CDP_URL",
    "ws://localhost:8411/v1/connect",
)
HARBOR_DEBUG_URL = os.getenv("HARBOR_DEBUG_URL", "ws://localhost:8411/v1/debug")


async def main() -> None:
    reference = str(uuid4())
    debug = asyncio.create_task(observe(HARBOR_DEBUG_URL, reference))
    await asyncio.sleep(0)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            with_reference(HARBOR_CDP_URL, reference)
        )
        page = await browser.new_page()

        await page.goto("https://example.com/?harbor-example=transition")
        heading = await page.evaluate("document.querySelector('h1').textContent")

        assert heading == "Example Domain"

        print("automatic provider transition succeeded")
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    attempts = [
        event["provider"] for event in events if event["event_type"] == "attempt.connected"
    ]
    transitions = [
        event for event in events if event["event_type"] == "execution.transitioned"
    ]
    assert len(attempts) >= 2
    assert len(transitions) == 1
    assert transitions[0]["payload"]["from_provider"] == attempts[0]
    assert transitions[0]["payload"]["to_provider"] == attempts[-1]


if __name__ == "__main__":
    asyncio.run(main())
