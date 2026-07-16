import asyncio
import logging
from dataclasses import replace
from uuid import uuid4

from backend.messaging import CapacityNotifier, PollingNotifier
from backend.proxy.attempts.capacity import provider_capacity
from backend.proxy.contracts import (
    AttemptState,
    HarborSession,
    ProviderAttempt,
    ResolvedSessionSettings,
)
from backend.proxy.errors import ProviderQueueFull, ProviderQueueTimeout
from backend.proxy.postgres import (
    AttemptAdmissionStatus,
    PostgresAttemptRepository,
)
from backend.settings import Settings

logger = logging.getLogger(__name__)


class AttemptLease:
    def __init__(
        self,
        attempt: ProviderAttempt,
        repository: PostgresAttemptRepository,
        notifier: CapacityNotifier,
    ) -> None:
        self.attempt = attempt
        self._repository = repository
        self._notifier = notifier
        self._released = False

    async def activate(self) -> None:
        if not await self._repository.activate(self.attempt):
            raise RuntimeError("Acquisition attempt is no longer claimable")
        self.attempt = replace(self.attempt, state=AttemptState.ACTIVE)

    async def release(
        self,
        *,
        failed: bool = False,
        reason: str = "client_disconnected",
    ) -> None:
        if self._released:
            return
        self._released = True
        released = await self._repository.finish(
            self.attempt,
            failed=failed,
            reason=reason,
        )
        if released:
            try:
                await self._notifier.notify(self.attempt.provider)
            except Exception:
                pass


class AttemptAdmission:
    def __init__(
        self,
        repository: PostgresAttemptRepository,
        settings: Settings,
        *,
        notifier: CapacityNotifier | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._notifier = notifier or PollingNotifier()

    async def acquire(
        self,
        session: HarborSession,
        resolved: ResolvedSessionSettings,
    ) -> AttemptLease:
        provider = resolved.provider.slug
        capacity = provider_capacity(self._settings, provider)
        attempt = ProviderAttempt(
            attempt_id=str(uuid4()),
            session_id=session.session_id,
            ordinal=0,
            provider=provider,
            state=AttemptState.REQUESTED,
        )
        try:
            status, attempt = await self._repository.enqueue(
                session,
                attempt.attempt_id,
                provider,
                max_active=capacity.max_active,
                max_queued=capacity.max_queued,
                resolved_settings={"harbor.provider.slug": provider.value},
                setting_sources={
                    field: source.value
                    for field, source in resolved.sources.items()
                    if field != "harbor.session.reference"
                },
            )
            if status is AttemptAdmissionStatus.FULL:
                raise ProviderQueueFull
            if status is AttemptAdmissionStatus.QUEUED:
                async with asyncio.timeout(self._settings.provider_queue_timeout_seconds):
                    while True:
                        claimed = await self._repository.claim(
                            session,
                            attempt,
                            max_active=capacity.max_active,
                        )
                        if claimed is not None:
                            attempt = claimed
                            break
                        await self._notifier.wait(
                            provider,
                            self._settings.provider_queue_poll_ms / 1000,
                        )
        except TimeoutError as error:
            await self._fail(attempt, "provider_queue_timeout")
            raise ProviderQueueTimeout from error
        except asyncio.CancelledError:
            await self._fail(attempt, "client_disconnected")
            raise
        return AttemptLease(attempt, self._repository, self._notifier)

    async def _fail(self, attempt: ProviderAttempt, reason: str) -> None:
        try:
            async with asyncio.timeout(self._settings.session_cleanup_timeout_seconds):
                await self._repository.finish(attempt, failed=True, reason=reason)
        except Exception:
            logger.exception(
                "Failed to release queued attempt %s within cleanup budget",
                attempt.attempt_id,
            )
