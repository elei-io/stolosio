import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

from backend.proxy.contracts import (
    RequestedSessionSettings,
    SessionState,
    StolosioSession,
)
from backend.proxy.errors import GatewayCapacityFull, SessionLeaseLost
from backend.proxy.postgres import (
    PostgresSessionRepository,
    SessionAdmissionStatus,
)
from backend.settings import Settings


class SessionLease:
    def __init__(
        self,
        session: StolosioSession,
        repository: PostgresSessionRepository,
        heartbeat_seconds: float,
    ) -> None:
        self.session = session
        self._repository = repository
        self._heartbeat_seconds = heartbeat_seconds
        self._lost = asyncio.Event()
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        self._released = False

    async def open(self) -> None:
        if not await self._repository.open(self.session):
            raise SessionLeaseLost
        self.session = replace(self.session, state=SessionState.OPEN)

    async def wait_lost(self) -> None:
        await self._lost.wait()

    async def release(
        self,
        *,
        failed: bool = False,
        reason: str = "client_disconnected",
    ) -> None:
        if self._released:
            return
        self._released = True
        self._heartbeat_task.cancel()
        await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        await self._repository.release(self.session, failed=failed, reason=reason)

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


class SessionAdmission:
    def __init__(
        self,
        repository: PostgresSessionRepository,
        settings: Settings,
        *,
        owner_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._owner_id = owner_id or str(uuid4())

    async def admit(self, requested: RequestedSessionSettings) -> SessionLease:
        session = StolosioSession(
            session_id=str(uuid4()),
            owner_id=self._owner_id,
            lease_token=str(uuid4()),
            state=SessionState.REQUESTED,
        )
        requested_settings = {
            **{field: "auto" for field in requested.auto_fields},
            **{
                field: str(value) if isinstance(value, UUID) else value
                for field, value in requested.overrides.items()
            },
        }
        status = await self._repository.admit(
            session,
            max_active=self._settings.stolosio_max_active_sessions,
            requested_settings=requested_settings,
            client_reference=(
                str(requested.session_reference)
                if requested.session_reference is not None
                else None
            ),
        )
        if status is SessionAdmissionStatus.FULL:
            raise GatewayCapacityFull
        return SessionLease(
            replace(session, state=SessionState.ADMITTED),
            self._repository,
            self._settings.session_heartbeat_seconds,
        )
