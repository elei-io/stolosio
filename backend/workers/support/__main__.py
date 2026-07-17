import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import async_playwright

from backend.db.session import engine, session_factory
from backend.proxy.capabilities import capability_registry
from backend.proxy.content_sanity import inspect_content
from backend.proxy.routing import RoutingRepository
from backend.proxy.support import SupportProbeResult, SupportRepository
from backend.settings import settings

logger = logging.getLogger(__name__)


def _probe_url(base: str, probe_id: str, provider: str) -> str:
    parsed = urlsplit(base)
    query = dict(parse_qsl(parsed.query))
    query["harbor.provider.slug"] = provider
    query["harbor.session.reference"] = probe_id
    return urlunsplit((*parsed[:3], urlencode(query), ""))


def _headers_state(headers: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    content_type = headers.get("content-type", "").lower()
    disposition = headers.get("content-disposition", "").lower()
    if content_type and not any(
        value in content_type for value in ("text/html", "application/xhtml+xml")
    ):
        return "unhealthy", ("non_html_content_type",)
    if "attachment" in disposition:
        return "unhealthy", ("download_response",)
    return "healthy", ()


async def _execute(job) -> SupportProbeResult:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            _probe_url(
                settings.support_harbor_cdp_url,
                job.id,
                job.provider.value,
            )
        )
        try:
            page = await browser.new_page()
            response = await page.goto(job.target_url)
            if response is None:
                return SupportProbeResult(
                    "unhealthy",
                    "unhealthy",
                    "inconclusive",
                    "inconclusive",
                    "inconclusive",
                    None,
                    ("navigation_without_response",),
                    len(job.required_methods),
                    0,
                    job.required_methods,
                    {},
                )
            await page.wait_for_timeout(250)
            content = await page.content()
            headers = await response.all_headers()
            headers_state, header_reasons = _headers_state(headers)
            status_state = "healthy" if 200 <= response.status < 300 else "unhealthy"
            unsupported = tuple(
                method
                for method in job.required_methods
                if not capability_registry.supports_observed_method(
                    job.provider, method
                )
            )
            method_coverage_state = "declared" if not unsupported else "missing"
            content_sanity = inspect_content(content)
            reasons = list(header_reasons)
            if status_state == "unhealthy":
                reasons.append("unhealthy_http_status")
            if unsupported:
                reasons.append("missing_declared_methods")
            reasons.extend(content_sanity.reason_codes)
            return SupportProbeResult(
                navigation_state="healthy",
                status_state=status_state,
                headers_state=headers_state,
                method_coverage_state=method_coverage_state,
                content_state=content_sanity.state,
                status_code=response.status,
                reason_codes=tuple(dict.fromkeys(reasons)),
                method_observed_count=len(job.required_methods),
                method_declared_count=len(job.required_methods) - len(unsupported),
                unsupported_methods=unsupported,
                content_facts=content_sanity.facts,
            )
        finally:
            await browser.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    owner = str(uuid4())
    repository = SupportRepository(session_factory)
    routing = RoutingRepository(session_factory)
    await routing.ensure_defaults()
    try:
        while True:
            await repository.schedule(delay_seconds=settings.support_schedule_delay_seconds)
            job = await repository.claim(
                owner,
                lease_seconds=settings.support_lease_seconds,
            )
            if job is None:
                await asyncio.sleep(settings.support_poll_seconds)
                continue
            try:
                result = await _execute(job)
                await repository.complete(job.id, owner, result)
            except Exception:
                logger.exception("Support probe %s failed", job.id)
                await repository.fail(job.id, owner)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
