import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import async_playwright

from backend.db.session import engine, session_factory
from backend.proxy.content_sanity import inspect_content, inspect_headers
from backend.proxy.health import (
    HealthProbeJob,
    HealthProbeResult,
    PromotionRepository,
)
from backend.proxy.routing import RoutingRepository
from backend.settings import settings

logger = logging.getLogger(__name__)


def _probe_url(base: str, probe_id: str, provider: str) -> str:
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query))
    query["harbor.provider.slug"] = provider
    query["harbor.session.reference"] = probe_id
    return urlunsplit((*parsed[:3], urlencode(query), ""))


async def _execute(job: HealthProbeJob) -> HealthProbeResult:
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, "chro" + "mium")
        browser = await browser_type.connect_over_cdp(
            _probe_url(
                settings.health_harbor_cdp_url,
                job.id,
                job.provider.value,
            )
        )
        try:
            page = await browser.new_page()
            response = await page.goto(
                job.target_url,
                wait_until="domcontentloaded",
            )
            if response is None:
                return HealthProbeResult(
                    "unhealthy",
                    "inconclusive",
                    "inconclusive",
                    "inconclusive",
                    None,
                    ("navigation_without_response",),
                    {},
                )
            if job.provider.value != "http":
                await page.wait_for_timeout(
                    settings.health_browser_settle_seconds * 1000
                )
            content = await page.content()
            headers = await response.all_headers()
            header_sanity = inspect_headers(headers)
            status_state = (
                "healthy" if 200 <= response.status < 300 else "unhealthy"
            )
            content_sanity = inspect_content(content)
            reasons = list(header_sanity.reason_codes)
            if status_state == "unhealthy":
                reasons.append("unhealthy_http_status")
            reasons.extend(content_sanity.reason_codes)
            return HealthProbeResult(
                navigation_state="healthy",
                status_state=status_state,
                headers_state=header_sanity.state,
                content_state=content_sanity.state,
                status_code=response.status,
                reason_codes=tuple(dict.fromkeys(reasons)),
                content_facts=content_sanity.facts,
            )
        finally:
            await browser.close()


async def _execute_and_record(
    repository: PromotionRepository,
    job: HealthProbeJob,
    owner: str,
) -> None:
    try:
        result = await _execute(job)
        await repository.complete(job.id, owner, result)
    except Exception:
        logger.exception("Health probe %s failed", job.id)
        await repository.fail(job.id, owner)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    owner = str(uuid4())
    repository = PromotionRepository(session_factory)
    routing = RoutingRepository(session_factory)
    await routing.ensure_defaults()
    try:
        while True:
            await repository.schedule(
                delay_seconds=settings.health_schedule_delay_seconds
            )
            jobs = await repository.claim_cohort(
                owner,
                lease_seconds=settings.health_lease_seconds,
            )
            if not jobs:
                await asyncio.sleep(settings.health_poll_seconds)
                continue
            await asyncio.gather(
                *(_execute_and_record(repository, job, owner) for job in jobs)
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
