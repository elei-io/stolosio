import asyncio
from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

from backend.messaging import CapacityNotifier, PollingNotifier
from backend.proxy.contracts import (
    HarborSession,
    RequestedSessionSettings,
    ResolvedSessionSettings,
    SessionState,
)
from backend.proxy.errors import ProviderQueueFull, SessionLeaseLost, SessionQueueTimeout
from backend.proxy.postgres import AdmissionStatus, PostgresSessionRepository
from backend.proxy.sessions.capacity import provider_capacity
from backend.settings import Settings


class SessionLease:
    def __init__(
        self,
        session: HarborSession,
        repository: PostgresSessionRepository,
        notifier: CapacityNotifier,
        heartbeat_seconds: float,
    ) -> None:
        self.session = session
        self._repository = repository
        self._notifier = notifier
        self._heartbeat_seconds = heartbeat_seconds
        self._lost = asyncio.Event()
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        self._released = False

    async def connected(self) -> None:
        if not await self._repository.transition(self.session, SessionState.CONNECTED):
            raise SessionLeaseLost
        self.session = replace(self.session, state=SessionState.CONNECTED)

    async def wait_lost(self) -> None:
        await self._lost.wait()

    async def release(self, *, failed: bool = False, reason: str = "client_disconnected") -> None:
        if self._released:
            return
        self._released = True
        self._heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._heartbeat_task

        if self.session.state is SessionState.CONNECTED:
            await self._repository.transition(self.session, SessionState.CLOSING)
        released = await self._repository.release(self.session, failed=failed, reason=reason)
        if released:
            with suppress(Exception):
                await self._notifier.notify(self.session.resolved_provider)

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                if not await self._repository.heartbeat(self.session):
                    self._lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._lost.set()


class SessionManager:
    def __init__(
        self,
        repository: PostgresSessionRepository,
        settings: Settings,
        *,
        owner_id: str | None = None,
        notifier: CapacityNotifier | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._owner_id = owner_id or str(uuid4())
        self._notifier = notifier or PollingNotifier()

    async def admit(
        self,
        requested: RequestedSessionSettings,
        resolved: ResolvedSessionSettings,
    ) -> SessionLease:
        session = HarborSession(
            session_id=str(uuid4()),
            owner_id=self._owner_id,
            lease_token=str(uuid4()),
            requested_provider=requested.provider,
            resolved_provider=resolved.provider.slug,
            state=SessionState.REQUESTED,
        )
        capacity = provider_capacity(self._settings, resolved.provider.slug)
        status = await self._repository.admit(
            session,
            max_active=capacity.max_active,
            max_queued=capacity.max_queued,
            requested_settings={
                **{field: "auto" for field in requested.auto_fields},
                **requested.overrides,
            },
            resolved_settings={"harbor.provider.slug": resolved.provider.slug.value},
            setting_sources={
                field: source.value for field, source in resolved.sources.items()
            },
        )
        if status is AdmissionStatus.FULL:
            raise ProviderQueueFull
        if status is AdmissionStatus.QUEUED:
            session = replace(session, state=SessionState.QUEUED)
            try:
                async with asyncio.timeout(self._settings.session_queue_timeout_seconds):
                    while not await self._repository.claim(
                        session,
                        max_active=capacity.max_active,
                    ):
                        await self._notifier.wait(
                            resolved.provider.slug,
                            self._settings.session_queue_poll_ms / 1000,
                        )
            except TimeoutError as error:
                await self._repository.release(
                    session,
                    failed=True,
                    reason="session_queue_timeout",
                )
                raise SessionQueueTimeout from error
            except asyncio.CancelledError:
                await self._repository.release(
                    session,
                    failed=True,
                    reason="client_disconnected",
                )
                raise

        session = replace(session, state=SessionState.ACQUIRING)
        return SessionLease(
            session,
            self._repository,
            self._notifier,
            self._settings.session_heartbeat_seconds,
        )
