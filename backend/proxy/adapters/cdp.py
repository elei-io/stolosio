from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from websockets.asyncio.client import ClientConnection, connect

from backend.proxy.contracts import (
    ProviderName,
    ResolvedSessionSettings,
    StolosioSession,
)
from backend.proxy.transport.domain_blocking import apply_domain_blocking


class WebSocketProviderSession:
    def __init__(
        self,
        provider: ProviderName,
        websocket: ClientConnection,
        *,
        provider_session_id: str | None = None,
        provider_started_at: datetime | None = None,
        timeout_started_at: datetime | None = None,
        session_timeout_seconds: int | None = None,
    ) -> None:
        self.provider = provider
        self._websocket = websocket
        self._closed = False
        self.provider_session_id = provider_session_id
        self.provider_started_at = provider_started_at or datetime.now(UTC)
        self.provider_ended_at: datetime | None = None
        self._timeout_at = (
            (timeout_started_at or self.provider_started_at)
            + timedelta(seconds=session_timeout_seconds)
            if session_timeout_seconds is not None
            else None
        )

    @property
    def disconnect_reason(self) -> str | None:
        if (
            self._timeout_at is not None
            and datetime.now(UTC) >= self._timeout_at - timedelta(seconds=1)
        ):
            return "provider_timeout"
        return None

    async def send(self, message: str) -> None:
        await self._websocket.send(message)

    async def messages(self) -> AsyncIterator[str]:
        async for message in self._websocket:
            if not isinstance(message, str):
                raise RuntimeError("CDP provider sent a binary WebSocket message")
            yield message

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._websocket.close()
        self.provider_ended_at = datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DirectCdpAdapter:
    provider: ProviderName
    endpoint: str
    session_timeout_seconds: int | None = None

    async def acquire(
        self,
        session: StolosioSession,
        settings: ResolvedSessionSettings,
    ):
        acquisition_started_at = datetime.now(UTC)
        websocket = await connect(self.endpoint, max_size=None, proxy=None)
        return apply_domain_blocking(
            WebSocketProviderSession(
                self.provider,
                websocket,
                timeout_started_at=acquisition_started_at,
                session_timeout_seconds=self.session_timeout_seconds,
            ),
            settings.blocked_domain_patterns if settings is not None else (),
        )
