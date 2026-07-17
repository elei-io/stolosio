import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from websockets.asyncio.client import connect

from backend.proxy.contracts import ProviderName

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("HARBOR_E2E") != "1",
        reason="set HARBOR_E2E=1 with the Docker Compose stack running",
    ),
]


def harbor_url(provider: ProviderName) -> str:
    base = os.getenv("HARBOR_E2E_URL", "ws://localhost:8411/v1/connect")
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
    base = os.getenv("HARBOR_E2E_URL", "ws://localhost:8411/v1/connect")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(base)
        page = await browser.new_page()
        await page.goto("https://example.com")

        assert "Example Domain" in await page.content()
        await browser.close()


@pytest.mark.asyncio
async def test_no_browser_promotion_replays_all_prior_navigations() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(harbor_url(ProviderName.HTTP))
        page = await browser.new_page()
        await page.goto("https://example.com/?harbor-replay=first")
        assert "Example Domain" in await page.content()
        await page.goto("https://example.com/?harbor-replay=second")
        assert "Example Domain" in await page.content()

        state = await page.evaluate(
            "({search: location.search, historyLength: history.length, "
            "heading: document.querySelector('h1').textContent})"
        )

        assert state == {
            "search": "?harbor-replay=second",
            "historyLength": state["historyLength"],
            "heading": "Example Domain",
        }
        assert state["historyLength"] >= 3
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "example",
    [
        "05_no_browser_http_only.py",
        "06_no_browser_promotion.py",
        "07_no_browser_history.py",
    ],
)
async def test_no_browser_example_programs(example: str) -> None:
    root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["HARBOR_CDP_URL"] = harbor_url(ProviderName.HTTP)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "examples" / example),
        cwd=root,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode == 0, (stdout + stderr).decode()


@pytest.mark.asyncio
async def test_automatic_routing_example_program() -> None:
    root = Path(__file__).parents[2]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(root / "examples" / "08_automatic_routing.py"),
        cwd=root,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode == 0, (stdout + stderr).decode()


@pytest.mark.asyncio
async def test_abandoned_provider_waiters_do_not_leak_capacity() -> None:
    async with async_playwright() as playwright:
        blockers = [
            await playwright.chromium.connect_over_cdp(harbor_url(ProviderName.CHROMIUM))
            for _ in range(2)
        ]

        async def abandon_waiter() -> None:
            with pytest.raises(PlaywrightTimeoutError):
                await playwright.chromium.connect_over_cdp(
                    harbor_url(ProviderName.CHROMIUM),
                    timeout=100,
                )

        await asyncio.gather(*(abandon_waiter() for _ in range(6)))
        await asyncio.gather(*(blocker.close() for blocker in blockers))

        browser = await playwright.chromium.connect_over_cdp(
            harbor_url(ProviderName.CHROMIUM),
            timeout=5_000,
        )
        page = await browser.new_page()
        await page.goto("https://example.com")
        assert "Example Domain" in await page.content()
        await browser.close()


@pytest.mark.asyncio
async def test_managed_chromium_fleet_packs_sessions_scales_and_returns_to_minimum() -> None:
    api = os.getenv("HARBOR_E2E_HTTP_URL", "http://localhost:8411")

    async def chromium_fleet() -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{api}/v1/fleet/providers")
            response.raise_for_status()
            return next(
                snapshot
                for snapshot in response.json()
                if snapshot["provider"] == ProviderName.CHROMIUM.value
            )

    async def wait_for(predicate, timeout_seconds: float = 20) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            fleet = await chromium_fleet()
            if predicate(fleet):
                return fleet
            await asyncio.sleep(0.1)
        raise TimeoutError("Managed Chromium fleet did not reach expected state")

    async with async_playwright() as playwright:
        first = await playwright.chromium.connect_over_cdp(harbor_url(ProviderName.CHROMIUM))
        second = await playwright.chromium.connect_over_cdp(harbor_url(ProviderName.CHROMIUM))
        first_page = await first.new_page()
        second_page = await second.new_page()
        await asyncio.gather(
            first_page.goto("https://example.com"),
            second_page.goto("https://example.org"),
        )
        assert await first_page.title() == "Example Domain"
        assert await second_page.title() == "Example Domain"

        packed = await wait_for(
            lambda fleet: (
                fleet["observed_instances"] == 1
                and fleet["active_attempts"] == 2
                and fleet["available_slots"] == 0
            )
        )
        assert packed["total_slots"] == 2

        third_task = asyncio.create_task(
            playwright.chromium.connect_over_cdp(
                harbor_url(ProviderName.CHROMIUM),
                timeout=20_000,
            )
        )
        scaled = await wait_for(
            lambda fleet: fleet["desired_instances"] == 2 and fleet["ready_instances"] == 2
        )
        assert scaled["total_slots"] == 4
        third = await third_task

        await first.close()
        assert await second_page.title() == "Example Domain"
        await asyncio.gather(second.close(), third.close())

    reduced = await wait_for(
        lambda fleet: (
            fleet["desired_instances"] == 1
            and fleet["observed_instances"] == 1
            and fleet["ready_instances"] == 1
        ),
        timeout_seconds=30,
    )
    assert reduced["active_attempts"] == 0
    assert reduced["queued_attempts"] == 0
    assert reduced["available_slots"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        ProviderName.BROWSERLESS,
        ProviderName.LIGHTPANDA,
        ProviderName.CAMOUFOX,
    ],
)
async def test_managed_single_slot_fleet_scales_instances(provider: ProviderName) -> None:
    api = os.getenv("HARBOR_E2E_HTTP_URL", "http://localhost:8411")

    async def provider_fleet() -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{api}/v1/fleet/providers")
            response.raise_for_status()
            return next(
                fleet for fleet in response.json() if fleet["provider"] == provider.value
            )

    async def wait_for(predicate, timeout_seconds: float = 30) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            fleet = await provider_fleet()
            if predicate(fleet):
                return fleet
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Managed {provider.value} fleet did not reach expected state")

    async with async_playwright() as playwright:
        first = await playwright.chromium.connect_over_cdp(
            harbor_url(provider)
        )
        second_task = asyncio.create_task(
            playwright.chromium.connect_over_cdp(
                harbor_url(provider),
                timeout=30_000,
            )
        )
        scaled = await wait_for(
            lambda fleet: fleet["desired_instances"] == 2 and fleet["ready_instances"] == 2
        )
        assert scaled["total_slots"] == 2
        second = await second_task
        await asyncio.gather(first.close(), second.close())

    reduced = await wait_for(
        lambda fleet: (
            fleet["desired_instances"] == 1
            and fleet["observed_instances"] == 1
            and fleet["ready_instances"] == 1
        )
    )
    assert reduced["total_slots"] == 1
    assert reduced["active_attempts"] == 0


@pytest.mark.asyncio
async def test_debug_stream_replays_session_start_and_tails_until_close() -> None:
    reference = str(uuid4())
    base = os.getenv("HARBOR_E2E_URL", "ws://localhost:8411/v1/connect")
    separator = "&" if "?" in base else "?"
    cdp_url = f"{base}{separator}harbor.provider.slug=chromium&harbor.session.reference={reference}"
    debug_base = os.getenv("HARBOR_DEBUG_URL", "ws://localhost:8411/v1/debug")
    debug_url = f"{debug_base}?harbor.session.reference={reference}"

    async def observe() -> list[dict]:
        observed = []
        async with connect(debug_url) as websocket:
            async for raw_event in websocket:
                observed.append(json.loads(raw_event))
        return observed

    debug = asyncio.create_task(observe())
    await asyncio.sleep(0)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        page = await browser.new_page()
        await page.goto("https://example.com")
        await browser.close()

    events = await asyncio.wait_for(debug, timeout=10)
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "session.requested"
    assert event_types[-1] == "session.closed"
    assert "navigation.response" in event_types
    assert len({event["event_id"] for event in events}) == len(events)
