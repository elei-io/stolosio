from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from backend.proxy.contracts import HarborSession, SessionState

_SCRIPTS = Path(__file__).with_name("scripts")
_SESSION_PREFIX = "harbor:v1:sessions:"
_EVENT_STREAM = "harbor:v1:session_events"


class AdmissionStatus(StrEnum):
    ACQUIRED = "acquiring"
    QUEUED = "queued"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class RepositorySettings:
    lease_ms: int
    queue_ttl_ms: int
    terminal_ttl_ms: int
    event_stream_maxlen: int


class RedisSessionRepository:
    def __init__(
        self,
        redis: Redis,
        settings: RepositorySettings,
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._scripts = {
            name: redis.register_script((_SCRIPTS / f"{name}.lua").read_text())
            for name in ("admit", "claim", "heartbeat", "transition", "release")
        }

    async def ping(self) -> None:
        await self._redis.ping()

    async def close(self) -> None:
        await self._redis.aclose()

    async def admit(
        self,
        session: HarborSession,
        *,
        max_active: int,
        max_queued: int,
    ) -> AdmissionStatus:
        result = await self._scripts["admit"](
            keys=[
                self._session_key(session.session_id),
                self._active_key(session),
                self._queue_key(session),
                self._sequence_key(session),
                _EVENT_STREAM,
            ],
            args=[
                session.session_id,
                session.owner_id,
                session.lease_token,
                session.requested_provider.value,
                session.resolved_provider.value,
                max_active,
                max_queued,
                self._settings.lease_ms,
                self._settings.queue_ttl_ms,
                self._settings.terminal_ttl_ms,
                self._settings.event_stream_maxlen,
                _SESSION_PREFIX,
            ],
        )
        status = self._text(result[0])
        return AdmissionStatus(status)

    async def claim(self, session: HarborSession, *, max_active: int) -> bool:
        result = await self._scripts["claim"](
            keys=[
                self._session_key(session.session_id),
                self._active_key(session),
                self._queue_key(session),
                _EVENT_STREAM,
            ],
            args=[
                session.session_id,
                session.owner_id,
                session.lease_token,
                session.resolved_provider.value,
                max_active,
                self._settings.lease_ms,
                self._settings.event_stream_maxlen,
                _SESSION_PREFIX,
            ],
        )
        return self._text(result[0]) == AdmissionStatus.ACQUIRED

    async def heartbeat(self, session: HarborSession) -> bool:
        result = await self._scripts["heartbeat"](
            keys=[self._session_key(session.session_id), self._active_key(session)],
            args=[
                session.session_id,
                session.owner_id,
                session.lease_token,
                self._settings.lease_ms,
            ],
        )
        return int(result) == 1

    async def transition(self, session: HarborSession, state: SessionState) -> bool:
        result = await self._scripts["transition"](
            keys=[self._session_key(session.session_id), _EVENT_STREAM],
            args=[
                session.session_id,
                session.owner_id,
                session.lease_token,
                session.requested_provider.value,
                session.resolved_provider.value,
                state.value,
                self._settings.lease_ms,
                self._settings.event_stream_maxlen,
            ],
        )
        return int(result) == 1

    async def release(
        self,
        session: HarborSession,
        *,
        failed: bool,
        reason: str,
    ) -> bool:
        result = await self._scripts["release"](
            keys=[
                self._session_key(session.session_id),
                self._active_key(session),
                self._queue_key(session),
                _EVENT_STREAM,
            ],
            args=[
                session.session_id,
                session.owner_id,
                session.lease_token,
                session.requested_provider.value,
                session.resolved_provider.value,
                "failed" if failed else "closed",
                reason,
                self._settings.terminal_ttl_ms,
                self._settings.event_stream_maxlen,
            ],
        )
        return int(result) == 1

    async def read_session(self, session_id: str) -> dict[str, str]:
        raw: dict[Any, Any] = await self._redis.hgetall(self._session_key(session_id))
        return {self._text(key): self._text(value) for key, value in raw.items()}

    async def active_count(self, provider: str) -> int:
        return await self._redis.zcard(f"harbor:v1:providers:{provider}:active")

    async def queue(self, provider: str) -> list[str]:
        values = await self._redis.zrange(f"harbor:v1:providers:{provider}:queue", 0, -1)
        return [self._text(value) for value in values]

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_PREFIX}{session_id}"

    @staticmethod
    def _active_key(session: HarborSession) -> str:
        return f"harbor:v1:providers:{session.resolved_provider.value}:active"

    @staticmethod
    def _queue_key(session: HarborSession) -> str:
        return f"harbor:v1:providers:{session.resolved_provider.value}:queue"

    @staticmethod
    def _sequence_key(session: HarborSession) -> str:
        return f"harbor:v1:providers:{session.resolved_provider.value}:queue_sequence"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)
