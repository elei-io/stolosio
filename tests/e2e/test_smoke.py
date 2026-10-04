"""Acceptance gate without external websites or paid browser providers."""

import asyncio
import base64
import os
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import async_playwright

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.getenv("STOLOSIO_E2E") != "1", reason="set STOLOSIO_E2E=1"),
]


@pytest.mark.asyncio
async def test_cdp_capture_and_capacity_cleanup(local_challenge_site) -> None:
    api = os.getenv("STOLOSIO_E2E_HTTP_URL", "http://localhost:8411")
    cdp = os.getenv("STOLOSIO_E2E_URL", "ws://localhost:8411/v1/connect")
    url = f"{local_challenge_site}/article/{uuid4().hex}"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp, timeout=15_000)
        try:
            page = await browser.new_page()
            await page.goto(url)
            assert "Section 11" in await page.content()
        finally:
            await browser.close()

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{api}/v1/capture", json={"url": url})
        response.raise_for_status()
        result = response.json()
        assert result["outcome"] == "captured", result
        assert not result["evidence"]["cost"]["paid"]
        assert "Section 11" in base64.b64decode(result["document"]["body_base64"]).decode()
        async with asyncio.timeout(15):
            while True:
                response = await client.get(f"{api}/v1/fleet/providers")
                response.raise_for_status()
                fleet = next(f for f in response.json() if f["provider"] == "browserless")
                if fleet["active_attempts"] == fleet["queued_attempts"] == 0:
                    break
                await asyncio.sleep(0.1)
