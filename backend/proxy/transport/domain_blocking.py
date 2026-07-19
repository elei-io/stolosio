import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress

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
_BOOTSTRAP_TIMEOUT_SECONDS = 15
_CLOSED = object()


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
        self._internal_responses: dict[
            tuple[str, int],
            asyncio.Future[dict],
        ] = {}
        self._command_id_condition = asyncio.Condition()
        self._messages: asyncio.Queue[str | BaseException | object] = asyncio.Queue()
        self._upstream_messages: AsyncIterator[str] | None = None
        self._upstream_pump: asyncio.Task[None] | None = None
        self._configuration_tasks: dict[str, asyncio.Task[None]] = {}
        self._unconfigured_targets: set[str] = set()
        self._target_buffers: dict[str, deque[str]] = {}
        self._target_sessions: dict[str, str] = {}
        self._disconnect_reason: str | None = None
        self._closed = False
        self._terminal_enqueued = False

    @property
    def provider(self):
        return self._upstream.provider

    @property
    def disconnect_reason(self) -> str | None:
        return self._disconnect_reason or self._upstream.disconnect_reason

    def __getattr__(self, name: str):
        return getattr(self._upstream, name)

    async def send(self, message: str) -> None:
        if self._disconnect_reason is not None:
            raise DomainBlockingUnavailable
        key = self._command_key(message)
        if key is None:
            await self._upstream.send(message)
            return
        async with self._command_id_condition:
            await self._command_id_condition.wait_for(
                lambda: key not in self._internal_responses
            )
            self._downstream_command_ids.add(key)
        try:
            await self._upstream.send(message)
        except BaseException:
            async with self._command_id_condition:
                self._downstream_command_ids.discard(key)
                self._command_id_condition.notify_all()
            raise

    async def messages(self) -> AsyncIterator[str]:
        self._ensure_upstream_pump()
        while True:
            message = await self._messages.get()
            if message is _CLOSED:
                return
            if isinstance(message, BaseException):
                raise message
            yield str(message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = [
            task
            for task in (*self._configuration_tasks.values(), self._upstream_pump)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for response in self._internal_responses.values():
            if not response.done():
                response.cancel()
        await self._upstream.close()
        self._enqueue_terminal(_CLOSED)

    def _ensure_upstream_pump(self) -> None:
        if self._upstream_pump is not None or self._closed:
            return
        self._upstream_messages = self._upstream.messages().__aiter__()
        self._upstream_pump = asyncio.create_task(self._pump_upstream())

    async def _pump_upstream(self) -> None:
        assert self._upstream_messages is not None
        try:
            async for message in self._upstream_messages:
                await self._route_upstream_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._unconfigured_targets:
                await self._fail_closed(error)
            else:
                self._enqueue_terminal(error)
        else:
            if self._unconfigured_targets:
                await self._fail_closed(StopAsyncIteration())
            else:
                self._enqueue_terminal(_CLOSED)

    async def _route_upstream_message(self, message: str) -> None:
        try:
            value = json.loads(message)
        except json.JSONDecodeError:
            self._messages.put_nowait(message)
            return

        key = self._value_command_key(value)
        if key is not None:
            internal = self._internal_responses.get(key)
            if internal is not None:
                if not internal.done():
                    internal.set_result(value)
                await asyncio.sleep(0)
                return
            async with self._command_id_condition:
                self._downstream_command_ids.discard(key)
                self._command_id_condition.notify_all()

        attached_session = self._attached_page_session_id_value(value)
        if attached_session is not None:
            self._begin_target_configuration(attached_session, value)
            await asyncio.sleep(0)
        target_session = attached_session or self._message_target_session_id(value)
        if target_session in self._unconfigured_targets:
            self._target_buffers[target_session].append(message)
            return
        self._messages.put_nowait(message)

    def _begin_target_configuration(
        self,
        session_id: str,
        attached: dict,
    ) -> None:
        if session_id in self._configuration_tasks:
            return
        params = attached.get("params")
        target = params.get("targetInfo") if isinstance(params, dict) else None
        target_id = target.get("targetId") if isinstance(target, dict) else None
        if isinstance(target_id, str):
            self._target_sessions[target_id] = session_id
        self._unconfigured_targets.add(session_id)
        self._target_buffers[session_id] = deque()
        self._configuration_tasks[session_id] = asyncio.create_task(
            self._configure_target(session_id)
        )

    async def _configure_target(self, session_id: str) -> None:
        key: tuple[str, int] | None = None
        async with self._command_id_condition:
            command_id = self._internal_command_id
            key = (session_id, command_id)
            while (
                key in self._downstream_command_ids
                or key in self._internal_responses
            ):
                command_id -= 1
                key = (session_id, command_id)
            self._internal_command_id = command_id - 1
            response = asyncio.get_running_loop().create_future()
            self._internal_responses[key] = response
        command = {
            "id": command_id,
            "method": "Network.setBlockedURLs",
            "params": {"urls": list(self._url_patterns)},
            "sessionId": session_id,
        }
        try:
            await self._upstream.send(json.dumps(command, separators=(",", ":")))
            async with asyncio.timeout(_BOOTSTRAP_TIMEOUT_SECONDS):
                result = await response
            if "error" in result:
                raise DomainBlockingUnavailable
            self._secure_target(session_id)
        except asyncio.CancelledError:
            if not self._closed:
                raise
        except Exception as error:
            await self._fail_closed(error)
        finally:
            async with self._command_id_condition:
                if key is not None:
                    self._internal_responses.pop(key, None)
                self._command_id_condition.notify_all()
            self._configuration_tasks.pop(session_id, None)

    def _secure_target(self, session_id: str) -> None:
        buffered = self._target_buffers.pop(session_id, ())
        self._unconfigured_targets.discard(session_id)
        for message in buffered:
            self._messages.put_nowait(message)

    async def _fail_closed(self, cause: BaseException) -> None:
        if self._disconnect_reason is not None or self._closed:
            return
        self._disconnect_reason = DomainBlockingUnavailable.reason
        self._closed = True
        current = asyncio.current_task()
        tasks = [
            task
            for task in (*self._configuration_tasks.values(), self._upstream_pump)
            if task is not None and task is not current
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for response in self._internal_responses.values():
            if not response.done():
                response.cancel()
        self._target_buffers.clear()
        self._unconfigured_targets.clear()
        while not self._messages.empty():
            with suppress(asyncio.QueueEmpty):
                self._messages.get_nowait()
        with suppress(Exception):
            await self._upstream.close()
        error = DomainBlockingUnavailable()
        error.__cause__ = cause
        self._enqueue_terminal(error)

    def _enqueue_terminal(self, value: BaseException | object) -> None:
        if self._terminal_enqueued:
            return
        self._terminal_enqueued = True
        self._messages.put_nowait(value)

    @staticmethod
    def _command_key(message: str) -> tuple[str | None, int] | None:
        try:
            value = json.loads(message)
        except json.JSONDecodeError:
            return None
        return DomainBlockingProviderSession._value_command_key(value)

    @staticmethod
    def _value_command_key(value: dict) -> tuple[str | None, int] | None:
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
        return DomainBlockingProviderSession._attached_page_session_id_value(value)

    @staticmethod
    def _attached_page_session_id_value(value: dict) -> str | None:
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

    def _message_target_session_id(self, value: dict) -> str | None:
        session_id = value.get("sessionId")
        if isinstance(session_id, str):
            return session_id
        params = value.get("params")
        if not isinstance(params, dict):
            return None
        nested_session_id = params.get("sessionId")
        if isinstance(nested_session_id, str):
            return nested_session_id
        target = params.get("targetInfo")
        target_id = target.get("targetId") if isinstance(target, dict) else None
        if not isinstance(target_id, str):
            target_id = params.get("targetId")
        if isinstance(target_id, str):
            return self._target_sessions.get(target_id)
        return None


def apply_domain_blocking(
    upstream: ProviderSession,
    domain_patterns: tuple[str, ...],
) -> ProviderSession:
    if not domain_patterns:
        return upstream
    return DomainBlockingProviderSession(upstream, domain_patterns)
