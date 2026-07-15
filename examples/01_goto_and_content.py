"""Fetch page content through Harbor using Playwright's standard CDP client."""

import asyncio
import os

from playwright.async_api import async_playwright

HARBOR_CDP_URL = os.getenv(
    "HARBOR_CDP_URL",
    "ws://localhost:8000/v1/connect",
)


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(HARBOR_CDP_URL)
        page = await browser.new_page()

        response = await page.goto("https://example.com")
        content = await page.content()

        assert response is not None
        assert response.ok
        assert "Example Domain" in content

        print("goto + content succeeded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
