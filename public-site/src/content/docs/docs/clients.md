---
title: Connect your clients
description: Connect Playwright and CDP clients to the single Stolosio endpoint.
---

Stolosio exposes `WS /v1/connect`. A client that already connects over CDP should only need an endpoint URL change. Playwright's `connect_over_cdp()` is the relevant transport; this is not the Playwright `browser_type.connect()` protocol.

## Complete Python example

From a checkout with `uv sync --locked` completed, save this as `first_session.py`:

```python
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            "ws://localhost:8411/v1/connect"
        )
        try:
            page = await browser.new_page()
            await page.goto("https://example.com")
            print(await page.content())
        finally:
            await browser.close()


asyncio.run(main())
```

Run it with `uv run python first_session.py`. This connects to a remote browser gateway; you do not need to launch a local Playwright browser.

## Select a provider explicitly

Automatic routing is the default. To force a particular acquisition path, use the Stolosio query namespace:

```text
ws://localhost:8411/v1/connect?stolosio.provider.slug=browserless
```

Supported provider slugs are `http`, `browserless`, and `browserbase`. Explicit HTTP selection returns protocol errors for unsupported commands; it does not silently emulate browser behavior.

## Permit paid fallback

```text
ws://localhost:8411/v1/connect?stolosio.provider.allow_paid_fallback=true
```

Automatic routing attempts local candidates first. Browserbase fallback also requires credentials and enabled provider capacity. Direct Browserbase selection is still subject to admission limits.

## Close sessions

Close the browser connection in a `finally` block so exceptions in your client do not leave work running unnecessarily. Handle connection failures and protocol errors explicitly. Do not blindly retry operations with side effects.

## Compatibility boundary

The HTTP facade covers navigation, HTML retrieval, and a narrow bootstrap surface. Other commands trigger browser acquisition in automatic sessions. After acquisition, the selected browser remains authoritative for CDP behavior. See the [provider matrix](/docs/providers/) before choosing a forced provider.
