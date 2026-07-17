"""Prove that goto + content can complete through Harbor's HTTP execution path."""

import asyncio
import os
from uuid import uuid4

from _debug import observe, with_reference
from playwright.async_api import async_playwright

HARBOR_CDP_URL = os.getenv(
    "HARBOR_CDP_URL",
    "ws://localhost:8411/v1/connect?harbor.provider.slug=http",
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
        context = await browser.new_context(java_script_enabled=False)
        page = await context.new_page()

        response = await page.goto("https://example.com/?harbor-example=http-only")
        content = await page.content()

        assert response is not None and response.ok
        assert "Example Domain" in content

        print("HTTP-only goto + content succeeded")
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    attempts = [
        event["provider"] for event in events if event["event_type"] == "attempt.started"
    ]
    assert attempts == ["http"]
    assert all(event["event_type"] != "execution.transitioned" for event in events)


if __name__ == "__main__":
    asyncio.run(main())
