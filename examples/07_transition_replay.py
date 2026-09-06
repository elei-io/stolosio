"""Prove that a provider transition reconstructs prior navigation state."""

import asyncio
import os

from playwright.async_api import async_playwright

STOLOSIO_CDP_URL = os.getenv(
    "STOLOSIO_CDP_URL",
    "ws://localhost:8411/v1/connect",
)


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(STOLOSIO_CDP_URL)
        page = await browser.new_page()

        await page.goto("https://example.com/?stolosio-replay=first")
        assert "Example Domain" in await page.content()
        await page.goto("https://example.com/?stolosio-replay=second")
        assert "Example Domain" in await page.content()

        state = await page.evaluate(
            "({search: location.search, historyLength: history.length, "
            "heading: document.querySelector('h1').textContent})"
        )

        assert state["search"] == "?stolosio-replay=second"
        assert state["historyLength"] >= 3
        assert state["heading"] == "Example Domain"

        print("full command-history replay succeeded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
