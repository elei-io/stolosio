import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pagecapture import BqlBrowserTier, CaptureRequest, CaptureResult, CaptureService, Exclusion
from pagecapture import Settings as PageCaptureSettings
from pagecapture.classify import Classifier
from pagecapture.failures import failure
from pagecapture.labels import REASONS
from pagecapture.render import BrowserCapacity, Renderer
from pagecapture.service import now
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.errors import is_transient_database_error
from backend.db.models import CaptureResultRecord, GatewaySession, SessionEventRecord
from backend.events.registry import EventType, validate_payload
from backend.metrics.definitions import (
    CAPTURE_BROWSER_SECONDS,
    CAPTURE_DURATION,
    CAPTURE_PAID,
    CAPTURE_REJECTED,
    CAPTURES,
)
from backend.proxy.adapters.browserless_cloud import browserless_cloud_url
from backend.proxy.attempts import AttemptAdmission, AttemptLease
from backend.proxy.capture.analytics import capture_facts
from backend.proxy.capture.cache import PostgresMethodCache
from backend.proxy.capture.fetcher import StolosioFetcher
from backend.proxy.capture.tiers import CloudChallengeTier, SharedRenderer, SlotTier
from backend.proxy.contracts import (
    ProviderName,
    ProviderSettingSchema,
    RequestedSessionSettings,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SettingSource,
)
from backend.proxy.errors import GatewayCapacityFull, ProviderQueueFull, ProviderQueueTimeout
from backend.proxy.external_capacity import ExternalCapacityRepository
from backend.proxy.network_policy import NetworkPolicyRepository
from backend.proxy.sessions import SessionAdmission, SessionLease
from backend.settings import Settings

logger = logging.getLogger(__name__)


def capture_hostname(url: str) -> str | None:
    try:
        hostname = (urlsplit(url).hostname or "").encode("idna").decode().lower()
    except UnicodeError:
        return None
    return hostname if len(hostname) <= 253 else None


def capture_attempts(result: CaptureResult) -> list[dict[str, object]]:
    """Persist bounded facts, never free-text decisions, cache keys, URLs or renderer notes."""
    summaries = []
    for attempt in result.evidence.attempts:
        comparison = attempt.comparison or {}
        summaries.append(
            {
                "path": attempt.path,
                "tier": attempt.tier,
                "status_code": attempt.status_code,
                "duration_ms": attempt.duration_ms,
                "assessment": (
                    attempt.assessment.primary if attempt.assessment.primary in REASONS else None
                ),
                "decision": attempt.decision,
                "reason": attempt.reason_code,
                "http_coverage": comparison.get("http_coverage"),
                "http_sufficient": comparison.get("http_sufficient"),
            }
        )
    return summaries


# A provider wait must leave time for rendering within the tier's remaining budget.
MIN_RENDER_SECONDS = 5.0
RETRY_AFTER_SECONDS = 5
DATABASE_CONFLICT = "database_conflict"


class CaptureUnavailable(Exception):
    """No capacity to start the capture (a 503 with Retry-After)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = RETRY_AFTER_SECONDS


class _Capture:
    """One capture's hold on Stolosio capacity: its session, its local slot, and the cloud attempt
    that may replace the slot for challenge resolution."""

    def __init__(self, session: SessionLease) -> None:
        self.session = session
        self.slot: AttemptLease | None = None
        self.cloud: AttemptLease | None = None


class CaptureRunner:
    """Admit a logical capture session; acquire provider capacity only when rendering starts.
    Managed and local rendering reuse one slot, while cloud resolution may replace it.
    The request's exclusions are merged with the network policy."""

    def __init__(
        self,
        sessions: SessionAdmission,
        attempts: AttemptAdmission,
        network_policy: NetworkPolicyRepository,
        external_capacity: ExternalCapacityRepository,
        database_sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._attempts = attempts
        self._network_policy = network_policy
        self._external_capacity = external_capacity
        self._database_sessions = database_sessions
        self._settings = settings
        self._page_settings = PageCaptureSettings(
            browser_ws=None,
            challenge_browser_ws=None,
            method_cache_path=None,
            http_timeout_s=settings.http_request_timeout_seconds,
            http_max_response_bytes=settings.http_max_response_bytes,
            proxy_country=settings.browserless_cloud_proxy_country,
            capture_cap_s=settings.capture_default_deadline_ms / 1000,
        )
        self._fetcher = StolosioFetcher(self._page_settings, proxy=settings.http_fetch_proxy_url)
        self._renderer = SharedRenderer(Renderer(self._page_settings))
        self._local_renderer = SharedRenderer(
            Renderer(
                replace(
                    self._page_settings,
                    render_cap_s=self._page_settings.local_resolution_cap_s
                    - self._page_settings.local_resolution_progress_wait_s,
                    block_resources=(),
                ),
                user_agent=None,
                challenge_wait_s=self._page_settings.local_resolution_wait_s,
                challenge_progress_wait_s=self._page_settings.local_resolution_progress_wait_s,
                intercept=False,
            )
        )
        self._cloud = (
            BqlBrowserTier(
                browserless_cloud_url(
                    str(settings.browserless_cloud_url),
                    settings.browserless_cloud_token,
                    path="/stealth/bql",
                    proxy_country=settings.browserless_cloud_proxy_country,
                    websocket=False,
                ),
                settings=self._page_settings,
                proxied=True,
            )
            if settings.browserless_cloud_token
            else None
        )
        self._classifier = Classifier(self._page_settings)
        self._cache = PostgresMethodCache(database_sessions)

    async def close(self) -> None:
        for part in (self._fetcher, self._renderer, self._local_renderer, self._cloud):
            if part is not None:
                await part.close()

    async def capture(self, request: CaptureRequest) -> CaptureResult:
        try:
            return await self._capture(request)
        except Exception as error:
            # Admission retries deadlocks and serialization failures; one that outlasts those
            # retries is still a conflict the caller can retry, not a server error.
            if is_transient_database_error(error):
                raise self._unavailable(DATABASE_CONFLICT) from error
            raise

    async def _capture(self, request: CaptureRequest) -> CaptureResult:
        started = time.monotonic()
        deadline_s = (
            min(
                request.deadline_ms or self._settings.capture_default_deadline_ms,
                self._settings.capture_default_deadline_ms,
            )
            / 1000
        )
        started_at = now()
        deadline_at = started + deadline_s
        try:
            async with asyncio.timeout_at(deadline_at):
                policy = await self._network_policy.settings()
        except TimeoutError as error:
            raise self._unavailable("session_admission_timeout") from error
        resolved = ResolvedSessionSettings(
            provider=ProviderSettingSchema(slug=ProviderName.BROWSERLESS),
            session=SessionSettingSchema(),
            sources={"stolosio.provider.slug": SettingSource.EXPLICIT},
            blocked_domain_patterns=policy.blocked_domain_patterns,
            network_policy_version=policy.configuration_version,
        )
        admission = asyncio.create_task(
            self._sessions.admit(
                RequestedSessionSettings(),
                workload="capture",
                capture_hostname=capture_hostname(request.url),
            )
        )
        try:
            async with asyncio.timeout_at(deadline_at):
                session = await asyncio.shield(admission)
        except (asyncio.CancelledError, TimeoutError) as error:
            # A committed admission may finish as its caller times out. Drain the task
            # and release any late lease rather than abandoning global capacity.
            admission.cancel()
            [late] = await asyncio.gather(admission, return_exceptions=True)
            reason = (
                "session_admission_timeout"
                if isinstance(error, TimeoutError)
                else "client_disconnected"
            )
            if isinstance(late, SessionLease):
                await self._bounded(late.release(failed=True, reason=reason), "late session")
            if isinstance(error, TimeoutError):
                raise self._unavailable(reason) from error
            raise
        except GatewayCapacityFull as error:
            raise self._unavailable(error.reason) from error
        capture = _Capture(session)
        failed, reason = True, "capture_failed"
        result: CaptureResult | None = None
        try:
            try:
                async with asyncio.timeout_at(deadline_at):
                    await session.open()
                    challenge_tier = await self._challenge_tier(request, capture, resolved)
            except TimeoutError:
                result = CaptureResult(
                    outcome="failed",
                    requested_url=request.url,
                    final_url=request.url,
                    started_at=started_at,
                    finished_at=now(),
                    reference=request.reference,
                    failure=failure(
                        "deadline_exceeded", "capture deadline expired before fetching"
                    ),
                )
                failed, reason = False, "capture_completed"
                return result

            async def endpoint(budget_s: float) -> str:
                return await self._local_slot(capture, resolved, budget_s)

            service = CaptureService(
                self._page_settings,
                fetcher=self._fetcher,
                managed=SlotTier(self._renderer, endpoint),
                local_resolution=SlotTier(self._local_renderer, endpoint, local=True),
                challenge_resolution=challenge_tier,
                classifier=self._classifier,
                cache=self._cache,
            )
            remaining_ms = max(1, round((deadline_s - (time.monotonic() - started)) * 1000))
            exclusions = request.exclusions + tuple(
                Exclusion(p) for p in policy.blocked_domain_patterns
            )
            result = await service.capture(
                replace(request, exclusions=exclusions, deadline_ms=remaining_ms)
            )
            failed, reason = False, "capture_completed"
            if result.failure is not None:
                # Never the message: it may carry the URL, and a URL may carry credentials.
                logger.warning(
                    "Capture failed: code=%s category=%s transient=%s session=%s",
                    result.failure.code,
                    result.failure.category,
                    result.failure.transient,
                    session.session.session_id,
                )
            return result
        except asyncio.CancelledError:
            reason = "client_disconnected"
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000)
            if result is not None:
                await self._bounded(
                    self._record(session, result, duration_ms, request.resolve_bot_challenges),
                    "capture event",
                )
            for attempt in (capture.cloud, capture.slot):
                if attempt is not None:
                    await self._bounded(attempt.release(failed=failed, reason=reason), "attempt")
            await self._bounded(session.release(failed=failed, reason=reason), "session")

    async def _local_slot(
        self, capture: _Capture, resolved: ResolvedSessionSettings, budget_s: float
    ) -> str:
        if capture.slot is None:
            wait_s = min(
                budget_s - MIN_RENDER_SECONDS, self._settings.provider_queue_timeout_seconds
            )
            if wait_s <= 0:
                raise TimeoutError("no time left to acquire browser capacity")
            try:
                async with asyncio.timeout(wait_s):
                    capture.slot = await self._attempts.acquire(capture.session.session, resolved)
                    await capture.slot.activate()
            except (ProviderQueueFull, ProviderQueueTimeout, TimeoutError) as error:
                raise BrowserCapacity(
                    "local browser capacity unavailable",
                    retry_after_seconds=RETRY_AFTER_SECONDS,
                ) from error
            except Exception as error:
                if is_transient_database_error(error):
                    raise BrowserCapacity(
                        DATABASE_CONFLICT, retry_after_seconds=RETRY_AFTER_SECONDS
                    ) from error
                raise
        endpoint = capture.slot.attempt.endpoint
        if endpoint is None:
            raise RuntimeError("assigned local browser instance has no endpoint")
        return endpoint

    async def _challenge_tier(
        self,
        request: CaptureRequest,
        capture: _Capture,
        resolved: ResolvedSessionSettings,
    ) -> CloudChallengeTier | None:
        """Only when the caller allows paid resolution and an operator enabled Browserless cloud."""
        if not request.resolve_bot_challenges or self._cloud is None:
            return None
        limit = await self._external_capacity.get(ProviderName.BROWSERLESS_CLOUD)
        if limit is None or not limit.enabled:
            return None
        cloud_settings = replace(
            resolved,
            provider=ProviderSettingSchema(slug=ProviderName.BROWSERLESS_CLOUD),
        )

        async def switch(deadline_s: float) -> None:
            try:
                async with asyncio.timeout(
                    max(1.0, min(deadline_s / 2, self._settings.provider_queue_timeout_seconds))
                ):
                    capture.cloud = await self._attempts.acquire(
                        capture.session.session,
                        cloud_settings,
                        replacement_for=(
                            capture.slot.attempt.attempt_id if capture.slot is not None else None
                        ),
                    )
            except (ProviderQueueFull, ProviderQueueTimeout, TimeoutError) as error:
                raise BrowserCapacity(
                    f"no Browserless cloud capacity: {error!r}",
                    retry_after_seconds=RETRY_AFTER_SECONDS,
                ) from error
            # The local slot is done: nothing renders locally after challenge resolution.
            if capture.slot is not None:
                await self._bounded(
                    capture.slot.release(failed=False, reason="challenge_resolution"),
                    "attempt",
                )
            await capture.cloud.activate()

        return CloudChallengeTier(self._cloud, switch)

    async def _record(
        self,
        session: SessionLease,
        result: CaptureResult,
        duration_ms: int,
        resolution_enabled: bool,
    ) -> None:
        tiers = list(dict.fromkeys(attempt.tier for attempt in result.evidence.attempts))
        last_tier = result.evidence.attempts[-1].tier if result.evidence.attempts else "direct"
        cost = result.evidence.cost
        failure = result.failure
        payload = validate_payload(
            EventType.CAPTURE_COMPLETED,
            {
                "outcome": result.outcome,
                "failure_code": failure.code if failure else None,
                "failure_category": failure.category if failure else None,
                "representation": result.document.representation if result.document else None,
                "tiers": tiers,
                "duration_ms": duration_ms,
                "browser_seconds": cost.browser_seconds,
                "paid": cost.paid,
                "bytes": cost.bytes,
                "attempts": capture_attempts(result),
            },
        )
        async with self._database_sessions.begin() as database:
            row = await database.get(
                GatewaySession, session.session.session_id, with_for_update=True
            )
            if row is None or row.capture_summary is not None:
                return
            row.capture_summary = payload
            recorded_at = datetime.now(UTC)
            inserted = await database.scalar(
                insert(CaptureResultRecord)
                .values(
                    session_id=session.session.session_id,
                    completed_at=recorded_at,
                    **capture_facts(result, resolution_enabled, duration_ms),
                )
                .on_conflict_do_nothing(index_elements=["session_id"])
                .returning(CaptureResultRecord.session_id)
            )
            if inserted is None:
                return
            database.add(
                SessionEventRecord(
                    session_id=session.session.session_id,
                    event_type=EventType.CAPTURE_COMPLETED.value,
                    provider=None,
                    reason=failure.code[:64] if failure else None,
                    occurred_at=recorded_at,
                    payload=payload,
                )
            )

        CAPTURES.labels(result.outcome, failure.category if failure else "none", last_tier).inc()
        CAPTURE_DURATION.labels(last_tier).observe(duration_ms / 1000)
        for attempt in result.evidence.attempts:
            if attempt.path == "browser":
                CAPTURE_BROWSER_SECONDS.labels(attempt.tier).inc(attempt.duration_ms / 1000)
        if cost.paid:
            CAPTURE_PAID.inc()

    @staticmethod
    def _unavailable(reason: str) -> CaptureUnavailable:
        CAPTURE_REJECTED.labels(reason).inc()
        return CaptureUnavailable(reason)

    async def _bounded(self, cleanup, resource: str) -> None:
        try:
            async with asyncio.timeout(self._settings.session_cleanup_timeout_seconds):
                await cleanup
        except Exception:
            logger.exception("Failed to finish Stolosio capture %s within cleanup budget", resource)
