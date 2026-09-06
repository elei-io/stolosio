"""Prove that goto + content can complete through Stolosio's HTTP execution path."""

import asyncio
import os
from uuid import uuid4

from _debug import observe, with_reference
from playwright.async_api import async_playwright

STOLOSIO_CDP_URL = os.getenv(
    "STOLOSIO_CDP_URL",
    "ws://localhost:8411/v1/connect?stolosio.provider.slug=http",
)
STOLOSIO_DEBUG_URL = os.getenv("STOLOSIO_DEBUG_URL", "ws://localhost:8411/v1/debug")


async def main() -> None:
    reference = str(uuid4())
    debug = asyncio.create_task(observe(STOLOSIO_DEBUG_URL, reference))
    await asyncio.sleep(0)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            with_reference(STOLOSIO_CDP_URL, reference)
        )
        context = await browser.new_context(java_script_enabled=False)
        page = await context.new_page()

        response = await page.goto("https://example.com/?stolosio-example=http-only")
        content = await page.content()

        assert response is not None and response.ok
        assert "Example Domain" in content

        print("HTTP-only goto + content succeeded")
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    attempts = [
        event["provider"] for event in events if event["event_type"] == "attempt.connected"
    ]
    assert attempts == ["http"]
    assert all(event["event_type"] != "execution.transitioned" for event in events)


if __name__ == "__main__":
    asyncio.run(main())
