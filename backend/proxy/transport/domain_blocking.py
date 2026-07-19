import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator

from backend.proxy.contracts import ProviderSession
from backend.proxy.errors import DomainBlockingUnavailable
from backend.proxy.network_policy import blocked_url_patterns

_BLOCKABLE_TARGET_TYPES = frozenset(
    {
        "background_page",
        "iframe",
        "page",
        "service_worker",
        "shared_worker",
        "webview",
        "worker",
    }
)
_BOOTSTRAP_TIMEOUT_SECONDS = 5


class DomainBlockingProviderSession:
    """Apply Harbor's URL blocklist before exposing newly attached page targets."""

    def __init__(
        self,
        upstream: ProviderSession,
        domain_patterns: tuple[str, ...],
    ) -> None:
        self._upstream = upstream
        self._url_patterns = blocked_url_patterns(domain_patterns)
        self._internal_command_id = -1
        self._downstream_command_ids: set[tuple[str | None, int]] = set()
        self._internal_command_ids: set[tuple[str, int]] = set()
        self._command_id_condition = asyncio.Condition()

    @property
    def provider(self):
        return self._upstream.provider

    @property
    def disconnect_reason(self) -> str | None:
        return self._upstream.disconnect_reason

    def __getattr__(self, name: str):
        return getattr(self._upstream, name)

    async def send(self, message: str) -> None:
        key = self._command_key(message)
        if key is None:
            await self._upstream.send(message)
            return
        async with self._command_id_condition:
            await self._command_id_condition.wait_for(
                lambda: key not in self._internal_command_ids
            )
            self._downstream_command_ids.add(key)
            await self._upstream.send(message)

    async def messages(self) -> AsyncIterator[str]:
        upstream_messages = self._upstream.messages().__aiter__()
        pending: deque[str] = deque()
        while True:
            try:
                message = (
                    pending.popleft()
                    if pending
                    else await anext(upstream_messages)
                )
            except StopAsyncIteration:
                return
            await self._complete_downstream_command(message)
            session_id = self._attached_page_session_id(message)
            if session_id is not None:
                buffered = await self._configure_target(
                    upstream_messages,
                    session_id,
                )
                pending.extend(buffered)
            yield message

    async def close(self) -> None:
        await self._upstream.close()

    async def _configure_target(
        self,
        upstream_messages: AsyncIterator[str],
        session_id: str,
    ) -> list[str]:
        async with self._command_id_condition:
            command_id = self._internal_command_id
            key = (session_id, command_id)
            while (
                key in self._downstream_command_ids
                or key in self._internal_command_ids
            ):
                command_id -= 1
                key = (session_id, command_id)
            self._internal_command_id = command_id - 1
            self._internal_command_ids.add(key)
        command = {
            "id": command_id,
            "method": "Network.setBlockedURLs",
            "params": {"urls": list(self._url_patterns)},
            "sessionId": session_id,
        }
        buffered: list[str] = []
        try:
            await self._upstream.send(json.dumps(command, separators=(",", ":")))
            async with asyncio.timeout(_BOOTSTRAP_TIMEOUT_SECONDS):
                while True:
                    message = await anext(upstream_messages)
                    try:
                        value = json.loads(message)
                    except json.JSONDecodeError:
                        buffered.append(message)
                        continue
                    if (
                        value.get("id") == command_id
                        and value.get("sessionId") == session_id
                    ):
                        if "error" in value:
                            raise DomainBlockingUnavailable
                        return buffered
                    buffered.append(message)
        except (TimeoutError, StopAsyncIteration) as error:
            raise DomainBlockingUnavailable from error
        finally:
            async with self._command_id_condition:
                self._internal_command_ids.discard(key)
                self._command_id_condition.notify_all()

    async def _complete_downstream_command(self, message: str) -> None:
        key = self._command_key(message)
        if key is None:
            return
        async with self._command_id_condition:
            self._downstream_command_ids.discard(key)

    @staticmethod
    def _command_key(message: str) -> tuple[str | None, int] | None:
        try:
            value = json.loads(message)
        except json.JSONDecodeError:
            return None
        command_id = value.get("id")
        if not isinstance(command_id, int):
            return None
        session_id = value.get("sessionId")
        return (session_id if isinstance(session_id, str) else None, command_id)

    @staticmethod
    def _attached_page_session_id(message: str) -> str | None:
        try:
            value = json.loads(message)
        except json.JSONDecodeError:
            return None
        if value.get("method") != "Target.attachedToTarget":
            return None
        params = value.get("params")
        if not isinstance(params, dict):
            return None
        target = params.get("targetInfo")
        session_id = params.get("sessionId")
        if (
            not isinstance(target, dict)
            or target.get("type") not in _BLOCKABLE_TARGET_TYPES
            or not isinstance(session_id, str)
        ):
            return None
        return session_id


def apply_domain_blocking(
    upstream: ProviderSession,
    domain_patterns: tuple[str, ...],
) -> ProviderSession:
    if not domain_patterns:
        return upstream
    return DomainBlockingProviderSession(upstream, domain_patterns)
