"""Observe a Stolosio session while controlling it through standard Playwright CDP."""

import asyncio
import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import async_playwright
from websockets.asyncio.client import connect

STOLOSIO_CDP_URL = os.getenv("STOLOSIO_CDP_URL", "ws://localhost:8411/v1/connect")
STOLOSIO_DEBUG_URL = os.getenv("STOLOSIO_DEBUG_URL", "ws://localhost:8411/v1/debug")


def with_reference(url: str, reference: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("stolosio.session.reference", reference))
    return urlunsplit((*parsed[:3], urlencode(query), parsed.fragment))


async def observe(reference: str) -> list[dict]:
    events = []
    async with connect(with_reference(STOLOSIO_DEBUG_URL, reference)) as websocket:
        async for raw_event in websocket:
            event = json.loads(raw_event)
            events.append(event)
            print(event["event_type"], event["payload"])
    return events


async def main() -> None:
    reference = str(uuid4())
    debug = asyncio.create_task(observe(reference))
    await asyncio.sleep(0)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            with_reference(STOLOSIO_CDP_URL, reference)
        )
        page = await browser.new_page()
        await page.goto("https://example.com")
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    assert events[0]["event_type"] == "session.open"
    assert events[-1]["event_type"] == "session.closed"


if __name__ == "__main__":
    asyncio.run(main())
