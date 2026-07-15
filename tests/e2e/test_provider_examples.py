import os

import pytest
from playwright.async_api import async_playwright

from backend.proxy.contracts import ProviderName

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("HARBOR_E2E") != "1",
        reason="set HARBOR_E2E=1 with the Docker Compose stack running",
    ),
]


def harbor_url(provider: ProviderName) -> str:
    base = os.getenv("HARBOR_E2E_URL", "ws://localhost:8000/v1/connect")
    return f"{base}?harbor.provider.slug={provider.value}"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(ProviderName))
async def test_goto_and_content(provider: ProviderName) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(harbor_url(provider))
        page = await browser.new_page()
        response = await page.goto("https://example.com")

        assert response is not None and response.ok
        assert "Example Domain" in await page.content()
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(ProviderName))
async def test_interaction(provider: ProviderName) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(harbor_url(provider))
        page = await browser.new_page()
        await page.goto("https://example.com")
        await page.locator("a").click()
        await page.wait_for_load_state()

        assert "iana.org" in page.url
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", list(ProviderName))
async def test_evaluate(provider: ProviderName) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(harbor_url(provider))
        page = await browser.new_page()
        await page.goto("https://example.com")

        assert await page.evaluate("document.querySelector('h1').textContent") == "Example Domain"
        await browser.close()


@pytest.mark.asyncio
async def test_omitted_provider_uses_automatic_plan() -> None:
    base = os.getenv("HARBOR_E2E_URL", "ws://localhost:8000/v1/connect")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(base)
        page = await browser.new_page()
        await page.goto("https://example.com")

        assert "Example Domain" in await page.content()
        await browser.close()
