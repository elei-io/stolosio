"""Capture admission, lazy provider capacity, exclusions, challenge resolution and accounting."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pagecapture import CaptureRequest, Exclusion, HttpResponse
from pagecapture.cache import MemoryMethodCache
from pagecapture.render import Rendered, content_lines
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.api.routes.capture import router
from backend.db.models import (
    AcquisitionAttempt,
    CaptureMethodCacheEntry,
    GatewaySession,
    SessionEventRecord,
)
from backend.fleet import FleetInstanceState, FleetRepository, ObservedInstance
from backend.messaging import PollingNotifier
from backend.proxy.attempts import AttemptAdmission
from backend.proxy.capture import CaptureRunner, CaptureUnavailable
from backend.proxy.contracts import ProviderName
from backend.proxy.external_capacity import ExternalCapacityRepository
from backend.proxy.network_policy import NetworkPolicyRepository
from backend.proxy.postgres import (
    PostgresAttemptRepository,
    PostgresSessionRepository,
    SessionRepositorySettings,
)
from backend.proxy.sessions import SessionAdmission
from backend.settings import Settings

ARTICLE = (
    "<html><head><title>A long story</title></head><body><main><h1>Headline</h1>"
    + "".join(
        f"<p>Paragraph {i} with enough words to count as real content here.</p>" for i in range(60)
    )
    + "</main></body></html>"
)
CHALLENGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Performing security verification</body></html>"
)


class FakeFetcher:
    def __init__(self, status=200, body=ARTICLE, headers=None, during=None):
        self.status, self.body = status, body
        self.headers = headers or [("Content-Type", "text/html; charset=utf-8")]
        self.exclusions = None
        self.during = during

    async def fetch(self, url, timeout_s, exclusions=(), accept=None):
        self.exclusions = exclusions
        if self.during is not None:
            await self.during()
        return HttpResponse(url, url, self.status, self.headers, self.body.encode(), [], 10.0)

    async def close(self):
        pass


def rendered(url: str, html: str) -> Rendered:
    text = BeautifulSoup(html, "lxml").get_text("\n")
    lines = content_lines(text)
    return Rendered(
        url=url,
        final_url=url,
        status=200,
        html=html,
        lines=lines,
        final_lines=len(lines),
        seconds=2.0,
        final_state={"chars": len(text), "mount": False, "pending": 0},
    )


class FakeRenderer:
    """Stands in for the shared Playwright renderer; `during` runs while the render holds the
    slot."""

    def __init__(self, html=ARTICLE, during=None):
        self.html, self.during, self.calls = html, during, []

    async def render(self, url, deadline_s, exclusions, endpoint):
        self.calls.append((endpoint, exclusions))
        if self.during is not None:
            await self.during()
        return rendered(url, self.html)

    async def close(self):
        pass


class FakeCloud:
    def __init__(self, html=ARTICLE):
        self.html, self.calls = html, 0

    async def render(self, url, deadline_s, exclusions=()):
        self.calls += 1
        return rendered(url, self.html)

    async def close(self):
        pass


@pytest.fixture
def capture_settings() -> Settings:
    return Settings(
        stolosio_max_active_sessions=8,
        session_lease_seconds=5,
        session_heartbeat_seconds=0.2,
        provider_queue_poll_ms=10,
        provider_queue_timeout_seconds=1,
        browserless_cloud_token="test-token",
    )


@pytest_asyncio.fixture
async def runner(
    database_sessions: async_sessionmaker[AsyncSession],
    capture_settings: Settings,
) -> CaptureRunner:
    fleets = FleetRepository(database_sessions)
    await fleets.ensure_fleet(
        ProviderName.BROWSERLESS,
        minimum_instances=1,
        maximum_instances=1,
        session_capacity_per_instance=1,
        scale_down_cooldown_seconds=1,
        max_queued_attempts=2,
    )
    await fleets.observe_instances(
        ProviderName.BROWSERLESS,
        [
            ObservedInstance(
                instance_id="browserless-test",
                endpoint="ws://browserless-test:3000",
                state=FleetInstanceState.READY,
                session_capacity=1,
            )
        ],
        platform="test",
        observation_ttl_seconds=30,
    )
    capacity = ExternalCapacityRepository(database_sessions, browserless_cloud_token="test-token")
    await capacity.ensure(
        ProviderName.BROWSERLESS_CLOUD,
        enabled=True,
        max_active_sessions=1,
        max_queued_attempts=1,
    )
    network_policy = NetworkPolicyRepository(database_sessions, cache_ttl_seconds=0.001)
    await network_policy.ensure_defaults()
    await network_policy.update(["blocked.test"])
    runner = CaptureRunner(
        SessionAdmission(
            PostgresSessionRepository(
                database_sessions,
                SessionRepositorySettings(lease_seconds=capture_settings.session_lease_seconds),
            ),
            capture_settings,
        ),
        AttemptAdmission(
            PostgresAttemptRepository(database_sessions),
            capture_settings,
            notifier=PollingNotifier(),
        ),
        network_policy,
        capacity,
        database_sessions,
        capture_settings,
    )
    runner._fetcher = FakeFetcher()
    runner._renderer = FakeRenderer()
    runner._local_renderer = FakeRenderer(html=CHALLENGE)
    runner._cloud = FakeCloud()
    yield runner
    await runner.close()


async def attempts(database: async_sessionmaker[AsyncSession]) -> list[AcquisitionAttempt]:
    async with database() as session:
        return list(
            await session.scalars(select(AcquisitionAttempt).order_by(AcquisitionAttempt.ordinal))
        )


@pytest.mark.asyncio
async def test_a_render_holds_one_local_slot_until_the_capture_answers(
    runner, database_sessions
) -> None:
    held = []

    async def during() -> None:
        held.extend(
            (a.provider, a.state, a.provider_instance_id) for a in await attempts(database_sessions)
        )

    runner._renderer = FakeRenderer(during=during)
    result = await runner.capture(CaptureRequest(url="https://example.test/page"))

    assert result.outcome == "captured" and result.document.representation == "response_body"
    assert held == [("browserless", "active", "browserless-test")]
    assert runner._renderer.calls[0][0] == "ws://browserless-test:3000"
    [attempt] = await attempts(database_sessions)
    assert (attempt.state, attempt.terminal_reason) == ("completed", "capture_completed")
    async with database_sessions() as database:
        session = await database.scalar(select(GatewaySession))
        events = list(
            await database.scalars(select(SessionEventRecord).order_by(SessionEventRecord.id))
        )
        cached = list(await database.scalars(select(CaptureMethodCacheEntry.key)))
    assert session.state == "closed"
    assert session.workload == "capture"
    assert session.capture_hostname == "example.test"
    [completed] = [e for e in events if e.event_type == "capture.completed"]
    assert completed.payload["outcome"] == "captured"
    assert completed.payload["tiers"] == ["direct", "managed"]
    assert session.capture_summary == completed.payload
    assert completed.payload["attempts"][0]["reason"] == "verify_http"
    assert completed.payload["attempts"][1]["http_sufficient"] is True
    assert completed.published_at is None  # an outbox row like every lifecycle event
    assert sorted(k.split(":")[0] for k in cached) == ["pattern", "url"]

    # Recording the same terminal result again must not duplicate its outbox event.
    await runner._record(
        SimpleNamespace(session=SimpleNamespace(session_id=session.id)), result, 9999, False
    )
    async with database_sessions() as database:
        repeated = list(
            await database.scalars(
                select(SessionEventRecord).where(
                    SessionEventRecord.event_type == "capture.completed"
                )
            )
        )
        row = await database.get(GatewaySession, session.id)
    assert len(repeated) == 1
    assert row.capture_summary == completed.payload


@pytest.mark.asyncio
async def test_the_network_policy_joins_the_request_exclusions(runner) -> None:
    own = Exclusion("ads.test")
    await runner.capture(CaptureRequest(url="https://example.test/page", exclusions=(own,)))

    assert runner._fetcher.exclusions == (own, Exclusion("blocked.test"))
    assert runner._renderer.calls[0][1] == (own, Exclusion("blocked.test"))


@pytest.mark.asyncio
async def test_browser_saturation_fails_verification_but_not_cached_http(
    runner, database_sessions
) -> None:
    runner._page_settings.canary_rate = 0.0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def hold() -> None:
        entered.set()
        await release.wait()

    # Warm the cache before occupying the only browser slot.
    await runner.capture(CaptureRequest(url="https://example.test/cached"))
    runner._renderer = FakeRenderer(during=hold)
    first = asyncio.create_task(runner.capture(CaptureRequest(url="https://example.test/a")))
    await entered.wait()
    try:
        cached = await runner.capture(CaptureRequest(url="https://example.test/cached"))
        assert cached.outcome == "captured" and [a.tier for a in cached.evidence.attempts] == [
            "direct"
        ]
        refused = await runner.capture(CaptureRequest(url="https://example.test/b"))
        assert refused.failure.code == "capacity" and refused.failure.retry_after_seconds > 0
        assert refused.document.body == ARTICLE.encode()
    finally:
        release.set()
        assert (await first).outcome == "captured"
    async with database_sessions() as database:
        sessions = list(await database.scalars(select(GatewaySession)))
    assert all(session.state == "closed" for session in sessions)
    # One warmed capture and the blocking render; the cache hit has no provider attempt.
    rows = await attempts(database_sessions)
    assert len([a for a in rows if a.state == "completed"]) == 2
    assert len([a for a in rows if a.state == "failed"]) == 1


class DeadlockDetected(RuntimeError):
    sqlstate = "40P01"


@pytest.mark.asyncio
async def test_http_fetch_does_not_reserve_a_provider_slot(runner, database_sessions):
    async def during():
        assert await attempts(database_sessions) == []
        async with database_sessions() as database:
            session = await database.scalar(select(GatewaySession))
        assert session.state == "open" and session.requested_settings == {}

    runner._fetcher = FakeFetcher(during=during)
    result = await runner.capture(CaptureRequest(url="https://example.test/page"))
    assert result.outcome == "captured"
    assert len(await attempts(database_sessions)) == 1


@pytest.mark.asyncio
async def test_http_only_work_still_obeys_the_global_session_limit(runner):
    runner._sessions._settings = runner._settings.model_copy(
        update={"stolosio_max_active_sessions": 1}
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()

    runner._fetcher = FakeFetcher(during=hold)
    first = asyncio.create_task(runner.capture(CaptureRequest(url="https://example.test/a")))
    await entered.wait()
    try:
        with pytest.raises(CaptureUnavailable) as refused:
            await runner.capture(CaptureRequest(url="https://example.test/b"))
        assert refused.value.reason == "gateway_capacity_full"
    finally:
        release.set()
        await first


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["http", "render"])
async def test_cancelled_capture_releases_only_the_capacity_it_acquired(
    runner, database_sessions, stage
):
    entered = asyncio.Event()

    async def hold():
        entered.set()
        await asyncio.Event().wait()

    if stage == "http":
        runner._fetcher = FakeFetcher(during=hold)
    else:
        runner._renderer = FakeRenderer(during=hold)
    task = asyncio.create_task(runner.capture(CaptureRequest(url="https://example.test/cancel")))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    rows = await attempts(database_sessions)
    assert len(rows) == (0 if stage == "http" else 1)
    assert all(a.state == "failed" and a.terminal_reason == "client_disconnected" for a in rows)
    async with database_sessions() as database:
        session = await database.scalar(select(GatewaySession))
    assert session.state == "failed" and session.terminal_reason == "client_disconnected"


@pytest.mark.asyncio
async def test_cancelling_a_queued_capture_releases_the_waiter(runner, database_sessions):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()

    runner._renderer = FakeRenderer(during=hold)
    first = asyncio.create_task(runner.capture(CaptureRequest(url="https://example.test/holder")))
    await entered.wait()
    queued = asyncio.Event()
    enqueue = runner._attempts._repository.enqueue

    async def observe_enqueue(*args, **kwargs):
        status, attempt = await enqueue(*args, **kwargs)
        if status.value == "queued":
            queued.set()
        return status, attempt

    runner._attempts._repository.enqueue = observe_enqueue
    waiter = asyncio.create_task(runner.capture(CaptureRequest(url="https://example.test/waiter")))
    try:
        async with asyncio.timeout(3):
            await queued.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        rows = await attempts(database_sessions)
        assert sorted(a.state for a in rows) == ["active", "failed"]
        assert next(a for a in rows if a.state == "failed").terminal_reason == "client_disconnected"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        release.set()
        await first


@pytest.mark.asyncio
async def test_expired_http_deadline_creates_no_provider_attempt(runner, database_sessions):
    async def hold():
        await asyncio.Event().wait()

    runner._fetcher = FakeFetcher(during=hold)
    async with asyncio.timeout(3):
        result = await runner.capture(
            CaptureRequest(url="https://example.test/slow", deadline_ms=100)
        )
    assert result.failure.code == "deadline_exceeded"
    assert await attempts(database_sessions) == []
    async with database_sessions() as database:
        session = await database.scalar(select(GatewaySession))
    assert session.state == "closed"


@pytest.mark.asyncio
async def test_admission_deadline_bounds_policy_lookup(runner, database_sessions):
    async def blocked_policy():
        await asyncio.Event().wait()

    runner._network_policy.settings = blocked_policy
    async with asyncio.timeout(3):
        with pytest.raises(CaptureUnavailable) as refused:
            await runner.capture(
                CaptureRequest(url="https://example.test/slow-policy", deadline_ms=100)
            )
    assert refused.value.reason == "session_admission_timeout"
    assert await attempts(database_sessions) == []
    async with database_sessions() as database:
        assert await database.scalar(select(GatewaySession)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_late_global_admission_is_drained_and_released(runner, database_sessions, cancel):
    admit = runner._sessions.admit
    entered = asyncio.Event()

    async def late_admit(*args, **kwargs):
        lease = await admit(*args, **kwargs)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return lease  # Model admission committing just as cancellation arrives.

    runner._sessions.admit = late_admit
    task = asyncio.create_task(
        runner.capture(CaptureRequest(url="https://example.test/late-admission", deadline_ms=500))
    )
    async with asyncio.timeout(3):
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(CaptureUnavailable):
                await task
    async with database_sessions() as database:
        session = await database.scalar(select(GatewaySession))
    assert session.state == "failed"
    assert session.terminal_reason == (
        "client_disconnected" if cancel else "session_admission_timeout"
    )
    assert await attempts(database_sessions) == []


@pytest.mark.asyncio
async def test_render_timeout_returns_a_deadline_failure_and_releases_the_slot(
    runner, database_sessions
):
    async def expired():
        raise TimeoutError("page render deadline expired")

    runner._renderer = FakeRenderer(during=expired)
    result = await runner.capture(CaptureRequest(url="https://example.test/timeout"))
    assert result.failure.code == "deadline_exceeded" and result.document.body == ARTICLE.encode()
    [attempt] = await attempts(database_sessions)
    assert attempt.state == "completed" and attempt.capacity_occupied_ms is not None


@pytest.mark.asyncio
async def test_cloud_can_resolve_when_no_local_slot_was_acquired(runner, database_sessions):
    fleets = FleetRepository(database_sessions)
    await fleets.observe_instances(
        ProviderName.BROWSERLESS, [], platform="test", observation_ttl_seconds=30
    )
    runner._fetcher = FakeFetcher(status=403, body=CHALLENGE)
    result = await runner.capture(
        CaptureRequest(url="https://example.test/cloud", resolve_bot_challenges=True)
    )
    assert result.outcome == "captured" and result.evidence.cost.paid
    assert runner._local_renderer.calls == [] and runner._cloud.calls == 1
    rows = await attempts(database_sessions)
    assert [(a.provider, a.state) for a in rows] == [
        ("browserless", "failed"),
        ("browserless_cloud", "completed"),
    ]


@pytest.mark.asyncio
async def test_a_browser_admission_conflict_is_a_completed_capacity_failure(
    runner, database_sessions
) -> None:
    repository = runner._attempts._repository
    enqueued = 0

    async def deadlocked(*args, **kwargs):
        nonlocal enqueued
        enqueued += 1
        raise DeadlockDetected("deadlock detected")

    repository.enqueue = deadlocked
    result = await runner.capture(CaptureRequest(url="https://example.test/page"))
    assert result.failure.code == "capacity" and result.failure.retry_after_seconds > 0
    assert result.document.body == ARTICLE.encode()
    assert enqueued == 3
    async with database_sessions() as database:
        states = list(await database.scalars(select(GatewaySession.state)))
    assert states == ["closed"]


@pytest.mark.asyncio
async def test_challenge_resolution_trades_the_local_slot_for_a_cloud_attempt(
    runner, database_sessions
) -> None:
    runner._fetcher = FakeFetcher(
        status=403,
        body=CHALLENGE,
        headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")],
    )
    result = await runner.capture(
        CaptureRequest(url="https://example.test/page", resolve_bot_challenges=True)
    )

    assert result.outcome == "captured" and result.evidence.cost.paid
    assert runner._cloud.calls == 1
    assert len(runner._local_renderer.calls) == 1
    assert [a.tier for a in result.evidence.attempts] == [
        "direct",
        "local_resolution",
        "challenge_resolution",
    ]
    local, cloud = await attempts(database_sessions)
    assert (local.provider, local.state, local.terminal_reason) == (
        "browserless",
        "completed",
        "challenge_resolution",
    )
    assert (cloud.provider, cloud.state) == ("browserless_cloud", "completed")


@pytest.mark.asyncio
async def test_resolution_requires_caller_permission(runner) -> None:
    runner._fetcher = FakeFetcher(
        status=403,
        body=CHALLENGE,
        headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")],
    )
    result = await runner.capture(CaptureRequest(url="https://example.test/page"))

    assert result.failure.code == "bot_challenge" and not result.failure.resolution_attempted
    assert len(runner._local_renderer.calls) == 0
    assert runner._cloud.calls == 0


@pytest.mark.asyncio
async def test_a_failed_capture_logs_its_code_and_category_but_not_the_url(runner, caplog) -> None:
    runner._fetcher = FakeFetcher(status=404, body="<html><title>404 Not Found</title></html>")
    with caplog.at_level("WARNING", logger="backend.proxy.capture.service"):
        result = await runner.capture(
            CaptureRequest(url="https://user:secret@example.test/missing?token=abc")
        )

    assert result.failure.code == "not_found"
    [record] = [r for r in caplog.records if r.getMessage().startswith("Capture failed")]
    message = record.getMessage()
    assert "code=not_found category=website transient=False" in message
    assert "example.test" not in message and "secret" not in message


@pytest.mark.asyncio
async def test_a_disabled_cloud_provider_means_no_challenge_tier(runner, database_sessions) -> None:
    await ExternalCapacityRepository(database_sessions).update(
        ProviderName.BROWSERLESS_CLOUD, {"enabled": False}, actor="test"
    )
    runner._fetcher = FakeFetcher(
        status=403,
        body=CHALLENGE,
        headers=[("Content-Type", "text/html"), ("cf-mitigated", "challenge")],
    )
    result = await runner.capture(
        CaptureRequest(url="https://example.test/page", resolve_bot_challenges=True)
    )

    assert result.failure.code == "bot_challenge" and runner._cloud.calls == 0


@pytest.mark.asyncio
async def test_local_resolution_reuses_the_slot_and_reports_to_the_outbox(
    runner, database_sessions
):
    runner._fetcher = FakeFetcher(body=CHALLENGE)
    runner._local_renderer = FakeRenderer()
    result = await runner.capture(
        CaptureRequest(url="https://example.test/local", resolve_bot_challenges=True)
    )
    assert result.outcome == "captured" and not result.evidence.cost.paid
    assert runner._cloud.calls == 0
    [local] = await attempts(database_sessions)
    assert local.provider == "browserless" and local.state == "completed"
    assert runner._local_renderer.calls == [
        ("ws://browserless-test:3000", (Exclusion("blocked.test"),))
    ]
    async with database_sessions() as database:
        completed = await database.scalar(
            select(SessionEventRecord).where(SessionEventRecord.event_type == "capture.completed")
        )
    assert completed.payload["tiers"] == ["direct", "local_resolution"]
    assert completed.payload["attempts"][-1] == {
        "tier": "local_resolution",
        "decision": "accept",
        "path": "browser",
        "status_code": 200,
        "duration_ms": 2000.0,
        "reason": "content_comparison",
        "http_coverage": 0.0,
        "http_sufficient": False,
    }
    assert completed.payload["browser_seconds"] == 2.0 and not completed.payload["paid"]
    assert "example.test" not in str(completed.payload)


# ---- the route ----


class FakeRunner:
    def __init__(self, error: Exception | None = None, *, verified: bool = True):
        self.error, self.requests, self.verified = error, [], verified

    async def capture(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        from pagecapture import CaptureService, Settings

        service = CaptureService(
            Settings(browser_ws=None, challenge_browser_ws=None, method_cache_path=None),
            fetcher=FakeFetcher(),
            managed=SimpleNamespace(tier="managed", paid=False, render=FakeCloud().render)
            if self.verified
            else None,
            cache=MemoryMethodCache(),
        )
        return await service.capture(request)


def client(runner: FakeRunner) -> TestClient:
    app = FastAPI()
    app.state.capture = runner
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("verified", [True, False])
def test_the_route_returns_every_capture_result_as_200(verified: bool) -> None:
    fake = FakeRunner(verified=verified)
    response = client(fake).post(
        "/v1/capture",
        json={"url": "https://example.test/page", "accept": ["text/html"], "reference": "r-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "2" and body["reference"] == "r-1"
    assert body["outcome"] == ("captured" if verified else "failed")
    if not verified:
        assert body["failure"]["code"] == "browser_unavailable" and body["document"] is not None
    assert fake.requests[0].accept == ("text/html",)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"url": "ftp://example.test/"}',
        b'{"url": "https://example.test/x", "exclusions": [{"host": "example.test"}]}',
    ],
)
def test_invalid_requests_are_400_and_never_captured(body: bytes) -> None:
    fake = FakeRunner()
    response = client(fake).post("/v1/capture", content=body)

    assert response.status_code == 400 and response.json()["error"] == "invalid_request"
    assert fake.requests == []


def test_no_capacity_is_503_with_retry_after_and_the_same_body_shape() -> None:
    response = client(FakeRunner(CaptureUnavailable("provider_queue_full"))).post(
        "/v1/capture", json={"url": "https://example.test/page"}
    )

    assert response.status_code == 503 and response.headers["retry-after"] == "5"
    body = response.json()
    assert body["outcome"] == "failed" and body["document"] is None
    assert body["failure"]["code"] == "capacity" and body["failure"]["transient"] is True


@pytest.mark.asyncio
async def test_capture_analytics_recording_is_atomic_and_idempotent(runner, database_sessions):
    from types import SimpleNamespace

    from backend.db.models import CaptureResultRecord
    from backend.proxy.capture.analytics import CaptureAnalytics

    result = await runner.capture(CaptureRequest(url="https://example.test/analytics"))
    async with database_sessions() as db:
        recorded = await db.scalar(select(CaptureResultRecord))
    lease = SimpleNamespace(session=SimpleNamespace(session_id=recorded.session_id))
    await runner._record(lease, result, 1000, False)
    stats = await CaptureAnalytics(database_sessions).overview("24h")
    assert stats["all_captures"]["total"] == 1
    async with database_sessions() as db:
        events = list(
            await db.scalars(
                select(SessionEventRecord).where(
                    SessionEventRecord.event_type == "capture.completed"
                )
            )
        )
    assert len(events) == 1
    assert events[0].payload["outcome"] == "captured"
    assert recorded.acquisition_outcome == "default"
