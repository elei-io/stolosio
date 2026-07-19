import asyncio
import logging
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from backend.messaging import CapacityNotifier, PollingNotifier
from backend.proxy.contracts import (
    AttemptState,
    HarborSession,
    ProviderAttempt,
    ResolvedSessionSettings,
    SettingSource,
)
from backend.proxy.errors import ProviderQueueFull, ProviderQueueTimeout
from backend.proxy.postgres import (
    AttemptAdmissionStatus,
    PostgresAttemptRepository,
)
from backend.settings import Settings

logger = logging.getLogger(__name__)

_TRANSIENT_DATABASE_STATES = {"40001", "40P01"}
_DATABASE_RELEASE_ATTEMPTS = 3


def _is_transient_database_error(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        sqlstate = getattr(current, "sqlstate", None) or getattr(
            current,
            "pgcode",
            None,
        )
        if sqlstate in _TRANSIENT_DATABASE_STATES:
            return True
        current = current.__cause__
    return False


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

    async def bind_provider_session(
        self,
        *,
        provider_session_id: str | None,
        provider_started_at: datetime | None,
    ) -> None:
        await self._repository.bind_provider_session(
            self.attempt,
            provider_session_id=provider_session_id,
            provider_started_at=provider_started_at,
        )

    async def record_provider_usage(
        self,
        *,
        provider_ended_at: datetime | None,
    ) -> None:
        await self._repository.record_provider_usage(
            self.attempt,
            provider_ended_at=provider_ended_at,
        )

    async def release(
        self,
        *,
        failed: bool = False,
        reason: str = "client_disconnected",
        command_summary: dict[str, object] | None = None,
    ) -> None:
        if self._released:
            return
        for release_attempt in range(1, _DATABASE_RELEASE_ATTEMPTS + 1):
            try:
                released = await self._repository.finish(
                    self.attempt,
                    failed=failed,
                    reason=reason,
                    command_summary=command_summary,
                )
                break
            except Exception as error:
                if (
                    release_attempt == _DATABASE_RELEASE_ATTEMPTS
                    or not _is_transient_database_error(error)
                ):
                    raise
                logger.warning(
                    "Retrying acquisition attempt %s release after transient "
                    "database error (%s/%s)",
                    self.attempt.attempt_id,
                    release_attempt,
                    _DATABASE_RELEASE_ATTEMPTS,
                )
                await asyncio.sleep(0)
        self._released = True
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
        *,
        replacement_for: str | None = None,
    ) -> AttemptLease:
        provider = resolved.provider.slug
        if provider is None:
            raise RuntimeError(
                "An acquisition attempt requires a concrete provider selection"
            )
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
                resolved_settings={
                    "harbor.provider.slug": provider.value,
                    "harbor.provider.allow_paid_fallback": (
                        resolved.provider.allow_paid_fallback
                    ),
                    "policy.network.blocked_domain_patterns": list(
                        resolved.blocked_domain_patterns
                    ),
                    "policy.network.configuration_version": (
                        resolved.network_policy_version
                    ),
                },
                setting_sources={
                    **{
                        field: source.value
                        for field, source in resolved.sources.items()
                        if field != "harbor.session.reference"
                    },
                    "policy.network.blocked_domain_patterns": (
                        SettingSource.POLICY.value
                    ),
                    "policy.network.configuration_version": (
                        SettingSource.POLICY.value
                    ),
                },
                replacement_for=replacement_for,
            )
            if status is AttemptAdmissionStatus.FULL:
                raise ProviderQueueFull
            if status is AttemptAdmissionStatus.QUEUED:
                async with asyncio.timeout(self._settings.provider_queue_timeout_seconds):
                    while True:
                        claimed = await self._repository.claim(
                            session,
                            attempt,
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
