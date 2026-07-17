import asyncio
import hashlib
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import async_playwright

from backend.db.session import engine, session_factory
from backend.events.normalization import filter_headers
from backend.proxy.qualification import ProbeResult, QualificationRepository
from backend.proxy.routing import RoutingRepository
from backend.settings import settings

logger = logging.getLogger(__name__)


def _probe_url(base: str, probe_id: str, provider: str) -> str:
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query))
    query["harbor.provider.slug"] = provider
    query["harbor.session.reference"] = probe_id
    return urlunsplit((*parsed[:3], urlencode(query), ""))


async def _execute(job) -> ProbeResult:
    console_errors = 0

    def observe_console(message) -> None:
        nonlocal console_errors
        if message.type == "error":
            console_errors += 1

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            _probe_url(
                settings.qualification_harbor_cdp_url,
                job.id,
                job.provider.value,
            )
        )
        try:
            page = await browser.new_page()
            page.on("console", observe_console)
            response = await page.goto(job.target_url)
            if response is None:
                raise RuntimeError("Qualification navigation returned no response")
            content = await page.content()
            return ProbeResult(
                status=response.status,
                headers=filter_headers(await response.all_headers()),
                console_errors=console_errors,
                content_fingerprint=hashlib.sha256(content.encode()).hexdigest(),
            )
        finally:
            await browser.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    owner = str(uuid4())
    repository = QualificationRepository(session_factory)
    routing = RoutingRepository(session_factory)
    await routing.ensure_defaults()
    try:
        while True:
            await repository.schedule(delay_seconds=settings.qualification_schedule_delay_seconds)
            job = await repository.claim(
                owner,
                lease_seconds=settings.qualification_lease_seconds,
            )
            if job is None:
                await asyncio.sleep(settings.qualification_poll_seconds)
                continue
            try:
                result = await _execute(job)
                await repository.complete(job.id, owner, result)
            except Exception:
                logger.exception("Qualification probe %s failed", job.id)
                await repository.fail(job.id, owner)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
