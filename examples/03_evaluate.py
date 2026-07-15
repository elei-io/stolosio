"""Evaluate JavaScript through Harbor's CDP endpoint."""

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

        await page.goto("https://example.com")
        heading = await page.evaluate("document.querySelector('h1').textContent")

        assert heading == "Example Domain"

        print("JavaScript evaluation succeeded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
