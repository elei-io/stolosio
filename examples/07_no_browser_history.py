"""Prove that promotion catches Chromium up through prior navigations."""

import asyncio
import os

from playwright.async_api import async_playwright

HARBOR_CDP_URL = os.getenv(
    "HARBOR_CDP_URL",
    "ws://localhost:8411/v1/connect?harbor.provider.slug=http",
)


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(HARBOR_CDP_URL)
        page = await browser.new_page()

        await page.goto("https://example.com/?harbor-replay=first")
        assert "Example Domain" in await page.content()
        await page.goto("https://example.com/?harbor-replay=second")
        assert "Example Domain" in await page.content()

        state = await page.evaluate(
            "({search: location.search, historyLength: history.length, "
            "heading: document.querySelector('h1').textContent})"
        )

        assert state["search"] == "?harbor-replay=second"
        assert state["historyLength"] >= 3
        assert state["heading"] == "Example Domain"

        print("full command-history replay succeeded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
