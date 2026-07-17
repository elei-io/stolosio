import asyncio
import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from backend.events.cdp import CdpEventObserver
from backend.proxy.adapters import get_provider_adapter
from backend.proxy.attempts import AttemptAdmission, AttemptLease
from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import (
    HarborSession,
    ProviderName,
    ProviderSession,
    ProviderSettingSchema,
    ResolvedSessionSettings,
    SettingSource,
)
from backend.proxy.provider_transition.history import ProviderTransitionRepository
from backend.proxy.provider_transition.materialization import (
    CdpReplayMaterializationStrategy,
)
from backend.proxy.routing import (
    NoSupportedProvider,
    ProviderCandidate,
    ProviderPlan,
    RoutingRepository,
)
from backend.settings import Settings

_CLOSED = object()
_UTILITY_EXPRESSION_SHA256_PREFIX = "6947ff8ef8a7"
_UTILITY_CALL = "(utilityScript, ...args) => utilityScript.evaluate(...args)"
_CONTENT_EXPRESSION = """() => {
        let retVal = "";
        if (document.doctype)
          retVal = new XMLSerializer().serializeToString(document.doctype);
        if (document.documentElement)
          retVal += document.documentElement.outerHTML;
        return retVal;
      }"""


def _http_request_headers(settings: Settings) -> dict[str, str]:
    return {
        "User-Agent": settings.http_user_agent,
        "Accept": settings.http_accept,
        "Accept-Language": settings.http_accept_language,
    }


_EMPTY_RESULT_METHODS = frozenset(
    {
        "Browser.setDownloadBehavior",
        "Browser.setWindowBounds",
        "Emulation.setDeviceMetricsOverride",
        "Emulation.setEmulatedMedia",
        "Emulation.setFocusEmulationEnabled",
        "Log.enable",
        "Network.enable",
        "Page.enable",
        "Page.setLifecycleEventsEnabled",
        "Runtime.runIfWaitingForDebugger",
        "Target.setAutoAttach",
    }
)


class ProviderTransitionError(RuntimeError):
    pass


class _Transition(Exception):
    def __init__(
        self,
        trigger: str,
        *,
        target: ProviderName | None = None,
        plan: ProviderPlan | None = None,
    ) -> None:
        super().__init__(trigger)
        self.trigger = trigger
        self.target = target
        self.plan = plan


@dataclass(slots=True)
class ReplayEntry:
    command: dict[str, Any]
    response: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class ProviderTransitionSession:
    """An automatic CDP facade that moves between verified provider attempts."""

    provider = None

    def __init__(
        self,
        session: HarborSession,
        resolved: ResolvedSessionSettings,
        attempts: AttemptAdmission | None,
        capabilities: CapabilityRegistry,
        history: ProviderTransitionRepository | None,
        observer: CdpEventObserver | None,
        settings: Settings,
        routing: RoutingRepository | None = None,
        *,
        automatic: bool = True,
        materialization: CdpReplayMaterializationStrategy | None = None,
    ) -> None:
        self._session = session
        self._resolved = resolved
        self._attempts = attempts
        self._capabilities = capabilities
        self._history = history
        self._observer = observer
        self._settings = settings
        self._routing = routing
        self._automatic = automatic
        self._materialization = materialization or CdpReplayMaterializationStrategy()

        self._messages: asyncio.Queue[str | object] = asyncio.Queue()
        self._closed = False
        self._failed = False
        self._failure_reason = "provider_connection_lost"
        self._attempt: AttemptLease | None = None
        self._upstream: ProviderSession | None = None
        self._upstream_messages: AsyncIterator[str] | None = None
        self._upstream_pump: asyncio.Task[None] | None = None

        self._replay: list[ReplayEntry] = []
        self._replay_bytes = 0
        self._current_entry: ReplayEntry | None = None
        self._forwarded: dict[int, ReplayEntry] = {}
        self._forward_event_entry: ReplayEntry | None = None
        self._forward_ids: dict[str, dict[object, object]] = defaultdict(dict)
        self._reverse_ids: dict[str, dict[object, object]] = defaultdict(dict)

        self._context_id = uuid4().hex.upper()
        self._target_id = uuid4().hex.upper()
        self._cdp_session_id = uuid4().hex.upper()
        self._frame_id = self._target_id
        self._loader_id = uuid4().hex.upper()
        self._execution_context_id = 1
        self._utility_context_id = 2
        self._utility_world_name = "__playwright_utility_world"
        self._utility_objects: set[str] = set()
        self._context_created = False
        self._target_created = False
        self._url = "about:blank"
        self._html = ""
        self._domain: str | None = None
        self._attempted_providers: set[ProviderName] = set()

    async def send(self, message: str) -> None:
        command = json.loads(message)
        if self._upstream is not None:
            provider = self._upstream.provider
            if not self._capabilities.supports(
                provider,
                command["method"],
                command.get("params"),
            ):
                if self._routing is not None and self._domain is not None:
                    try:
                        plan = await self._routing.plan(
                            self._domain,
                            required_commands=self._required_commands(command),
                            exclude=frozenset(
                                self._attempted_providers | {provider}
                            ),
                        )
                        await self._transition(command, command["method"], plan=plan)
                    except (NoSupportedProvider, ProviderTransitionError) as error:
                        await self._put(
                            self._response(
                                command,
                                error={"code": -32000, "message": str(error)},
                            )
                        )
                    return
            await self._forward(command)
            return

        if self._automatic and self._would_exceed_replay_budget(message):
            try:
                await self._transition(command, "replay_budget")
            except (NoSupportedProvider, ProviderTransitionError) as error:
                await self._put(
                    self._response(
                        command,
                        error={"code": -32000, "message": str(error)},
                    )
                )
            return

        entry = ReplayEntry(command=command)
        self._current_entry = entry
        try:
            result = await self._dispatch(command)
        except _Transition as transition:
            self._current_entry = None
            if not self._automatic:
                code = -32601 if transition.trigger == command["method"] else -32000
                await self._put(
                    self._response(
                        command,
                        error={
                            "code": code,
                            "message": (
                                f"{transition.trigger} is not supported by provider http"
                                if code == -32601
                                else transition.trigger
                            ),
                        },
                    )
                )
                return
            try:
                await self._transition(
                    command,
                    transition.trigger,
                    target=transition.target,
                    plan=transition.plan,
                )
            except (NoSupportedProvider, ProviderTransitionError) as error:
                await self._put(
                    self._response(
                        command,
                        error={"code": -32000, "message": str(error)},
                    )
                )
            return
        except Exception as error:
            self._current_entry = None
            await self._put(
                self._response(
                    command,
                    error={"code": -32000, "message": str(error)},
                )
            )
            return

        response = self._response(command, result=result)
        entry.response = response
        self._replay.append(entry)
        self._replay_bytes += len(message.encode())
        self._current_entry = None
        await self._put(response)

    async def messages(self) -> AsyncIterator[str]:
        while True:
            message = await self._messages.get()
            if message is _CLOSED:
                return
            yield str(message)

    async def fail(self, reason: str) -> None:
        self._failed = True
        self._failure_reason = reason

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._upstream_pump is not None:
            self._upstream_pump.cancel()
            await asyncio.gather(self._upstream_pump, return_exceptions=True)
        if self._upstream is not None:
            with suppress(Exception):
                await self._upstream.close()
        if self._attempt is not None:
            with suppress(Exception):
                await self._attempt.release(
                    failed=self._failed,
                    reason=self._failure_reason if self._failed else "client_disconnected",
                )
            self._attempt = None
        await self._messages.put(_CLOSED)

    async def _dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        method = command["method"]
        params = command.get("params")
        if not isinstance(params, dict):
            params = {}
        session_id = command.get("sessionId")

        if method == "Browser.getVersion":
            return {
                "protocolVersion": "1.3",
                "product": "Harbor/HTTP",
                "revision": "http",
                "userAgent": "Harbor HTTP acquisition",
                "jsVersion": "0",
            }
        if method in _EMPTY_RESULT_METHODS:
            return {}
        if method == "Target.getTargetInfo":
            return {"targetInfo": self._target_info()}
        if method == "Target.createBrowserContext":
            if self._context_created:
                raise _Transition(method)
            self._context_created = True
            return {"browserContextId": self._context_id}
        if method == "Target.disposeBrowserContext":
            if params.get("browserContextId") != self._context_id:
                raise _Transition(method)
            return {}
        if method == "Target.createTarget":
            if self._target_created:
                raise _Transition(method)
            self._target_created = True
            await self._event(
                "Target.attachedToTarget",
                {
                    "sessionId": self._cdp_session_id,
                    "targetInfo": self._target_info(),
                    "waitingForDebugger": True,
                },
                None,
            )
            return {"targetId": self._target_id}
        if method == "Browser.getWindowForTarget":
            return {
                "windowId": 1,
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": 1280,
                    "height": 720,
                    "windowState": "normal",
                },
            }
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": self._frame()}}
        if method == "Runtime.enable":
            await self._emit_execution_context(
                session_id,
                self._execution_context_id,
                "",
                True,
            )
            return {}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            world_name = params.get("worldName")
            if isinstance(world_name, str):
                self._utility_world_name = world_name
            return {"identifier": "1"}
        if method == "Page.createIsolatedWorld":
            self._utility_context_id += 1
            world_name = params.get("worldName")
            if isinstance(world_name, str):
                self._utility_world_name = world_name
            await self._emit_execution_context(
                session_id,
                self._utility_context_id,
                self._utility_world_name,
                False,
            )
            return {"executionContextId": self._utility_context_id}
        if method == "Emulation.setScriptExecutionDisabled":
            if not isinstance(params.get("value"), bool):
                raise ValueError("value must be a boolean")
            return {}
        if method == "Page.navigate":
            return await self._navigate(params, session_id)
        if method == "Runtime.evaluate" and self._is_utility_evaluation(params):
            object_id = f"harbor-http-utility-{uuid4().hex}"
            self._utility_objects.add(object_id)
            return {
                "result": {
                    "type": "object",
                    "className": "UtilityScript",
                    "description": "UtilityScript",
                    "objectId": object_id,
                }
            }
        if method == "Runtime.callFunctionOn" and self._is_content_call(params):
            return {"result": {"type": "string", "value": self._html}}
        if method == "Runtime.releaseObject":
            object_id = params.get("objectId")
            if object_id in self._utility_objects:
                self._utility_objects.discard(object_id)
                return {}
        raise _Transition(method)

    async def _navigate(
        self,
        params: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        url = params.get("url")
        if not isinstance(url, str):
            raise ValueError("Page.navigate requires a URL")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise _Transition("Page.navigate")
        self._domain = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()

        if self._attempt is None and self._automatic:
            if self._routing is None:
                raise ProviderTransitionError(
                    "Automatic provider planning is unavailable"
                )
            plan = await self._routing.plan(
                self._domain,
                required_commands=self._required_commands(),
            )
            candidate = plan.first
            if candidate.provider is not ProviderName.HTTP:
                raise _Transition(
                    "routing",
                    target=candidate.provider,
                    plan=plan,
                )
            await self._acquire_http_attempt(plan, candidate)

        request_headers = _http_request_headers(self._settings)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                trust_env=False,
                timeout=self._settings.http_request_timeout_seconds,
            ) as client:
                response = await client.get(url, headers=request_headers)
        except httpx.HTTPError as error:
            raise _Transition("http_transport_failure") from error
        if len(response.content) > self._settings.http_max_response_bytes:
            raise _Transition("http_response_too_large")

        self._url = str(response.url)
        self._html = response.text
        self._loader_id = uuid4().hex.upper()
        request_id = self._loader_id
        await self._event(
            "Page.frameStartedNavigating",
            {
                "frameId": self._frame_id,
                "url": url,
                "loaderId": self._loader_id,
                "navigationType": "differentDocument",
            },
            session_id,
        )
        await self._event("Page.frameStartedLoading", {"frameId": self._frame_id}, session_id)
        await self._event(
            "Network.requestWillBeSent",
            {
                "requestId": request_id,
                "loaderId": self._loader_id,
                "documentURL": url,
                "request": {
                    "url": url,
                    "method": "GET",
                    "headers": request_headers,
                    "initialPriority": "VeryHigh",
                    "referrerPolicy": "no-referrer-when-downgrade",
                },
                "timestamp": time.monotonic(),
                "wallTime": time.time(),
                "initiator": {"type": "other"},
                "type": "Document",
                "frameId": self._frame_id,
                "hasUserGesture": False,
            },
            session_id,
        )
        await self._event(
            "Network.responseReceived",
            {
                "requestId": request_id,
                "loaderId": self._loader_id,
                "timestamp": time.monotonic(),
                "type": "Document",
                "response": {
                    "url": self._url,
                    "status": response.status_code,
                    "statusText": response.reason_phrase,
                    "headers": dict(response.headers),
                    "mimeType": response.headers.get("content-type", "text/html").split(";")[0],
                    "connectionReused": False,
                    "connectionId": 0,
                    "encodedDataLength": len(response.content),
                    "securityState": "secure" if self._url.startswith("https:") else "neutral",
                    "fromDiskCache": False,
                    "fromServiceWorker": False,
                    "timing": self._network_timing(),
                },
                "hasExtraInfo": False,
                "frameId": self._frame_id,
            },
            session_id,
        )
        await self._emit_loaded_document(session_id, request_id, len(response.content))
        return {"frameId": self._frame_id, "loaderId": self._loader_id, "isDownload": False}

    async def _acquire_http_attempt(
        self, plan: ProviderPlan, candidate: ProviderCandidate
    ) -> None:
        assert self._attempts is not None
        assert self._observer is not None
        self._attempt = await self._attempts.acquire(
            self._session,
            self._settings_for(
                ProviderName.HTTP,
                SettingSource.AUTO,
            ),
        )
        await self._attempt.activate()
        self._attempted_providers.add(ProviderName.HTTP)
        if self._routing is not None and self._domain is not None:
            await self._routing.record_selection(
                self._attempt.attempt.attempt_id,
                self._domain,
                plan,
                candidate,
            )
        self._observer.bind_attempt(
            ProviderName.HTTP,
            UUID(self._attempt.attempt.attempt_id),
        )

    async def _transition(
        self,
        pending: dict[str, Any],
        trigger: str,
        *,
        target: ProviderName | None = None,
        plan: ProviderPlan | None = None,
    ) -> None:
        from_provider = (
            self._upstream.provider
            if self._upstream is not None
            else ProviderName.HTTP
            if self._attempt is not None
            else None
        )
        if self._upstream is not None:
            if not self._materialization.can_materialize(
                entry.command["method"] for entry in self._replay
            ):
                raise ProviderTransitionError(
                    "Cannot transition after commands with non-replay-safe side effects"
                )
            if any(entry.response is None for entry in self._forwarded.values()):
                raise ProviderTransitionError(
                    "Cannot transition with unacknowledged provider commands"
                )
        if plan is None:
            if self._routing is not None and self._domain is not None:
                plan = await self._routing.plan(
                    self._domain,
                    required_commands=self._required_commands(pending),
                    exclude=frozenset(self._attempted_providers),
                )
            elif self._routing is not None:
                settings = await self._routing.settings()
                selected_provider = target or settings.default_provider
                plan = ProviderPlan(
                    (ProviderCandidate(selected_provider, 0, 0),),
                    "configured_default",
                    settings.configuration_version,
                    settings.support_policy_version,
                )
            else:
                raise ProviderTransitionError(
                    "Automatic provider planning is unavailable"
                )
        source_attempt = self._attempt
        source_upstream = self._upstream
        source_messages = self._upstream_messages
        source_pump = self._upstream_pump
        source_forward_event_entry = self._forward_event_entry
        source_forward_ids = {
            kind: dict(values) for kind, values in self._forward_ids.items()
        }
        source_reverse_ids = {
            kind: dict(values) for kind, values in self._reverse_ids.items()
        }
        last_error: Exception | None = None
        assert self._attempts is not None
        assert self._observer is not None
        for selected in plan.candidates:
            target = selected.provider
            if target in self._attempted_providers or target is ProviderName.HTTP:
                continue
            target_attempt: AttemptLease | None = None
            upstream: ProviderSession | None = None
            self._attempted_providers.add(target)
            try:
                target_settings = self._settings_for(target, SettingSource.AUTO)
                target_attempt = await self._attempts.acquire(self._session, target_settings)
                adapter = get_provider_adapter(
                    target,
                    endpoint=target_attempt.attempt.endpoint,
                )
                async with asyncio.timeout(
                    self._settings.provider_acquisition_timeout_seconds
                ):
                    upstream = await adapter.acquire(self._session, target_settings)
                await target_attempt.activate()
                self._upstream = upstream
                self._upstream_messages = upstream.messages().__aiter__()
                self._forward_ids.clear()
                self._reverse_ids.clear()
                await self._replay_history()
                if self._routing is not None and self._domain is not None:
                    await self._routing.record_selection(
                        target_attempt.attempt.attempt_id,
                        self._domain,
                        plan,
                        selected,
                        transition_trigger=trigger if from_provider is not None else None,
                    )
                await self._forward(pending)
                self._observer.bind_attempt(target, UUID(target_attempt.attempt.attempt_id))
                self._attempt = target_attempt
                if source_pump is not None:
                    source_pump.cancel()
                    await asyncio.gather(source_pump, return_exceptions=True)
                if source_upstream is not None:
                    with suppress(Exception):
                        await source_upstream.close()
                if source_attempt is not None:
                    with suppress(Exception):
                        await source_attempt.release(
                            failed=False, reason="provider_transitioned"
                        )
                self._upstream_pump = asyncio.create_task(self._pump_upstream())
                if from_provider is not None:
                    with suppress(Exception):
                        await self._emit_transition(
                            from_provider.value,
                            target,
                            trigger,
                        )
                return
            except Exception as error:
                last_error = error
                if upstream is not None:
                    with suppress(Exception):
                        await upstream.close()
                if target_attempt is not None:
                    with suppress(Exception):
                        await target_attempt.release(
                            failed=True, reason="provider_transition_attempt_failed"
                        )
                self._attempt = source_attempt
                self._upstream = source_upstream
                self._upstream_messages = source_messages
                self._upstream_pump = source_pump
                self._forward_ids = defaultdict(dict, source_forward_ids)
                self._reverse_ids = defaultdict(dict, source_reverse_ids)
                self._forward_event_entry = source_forward_event_entry
        raise ProviderTransitionError("Supported provider plan exhausted") from last_error

    def _required_commands(
        self, pending: dict[str, Any] | None = None
    ) -> tuple[tuple[str, dict | None], ...]:
        commands = [
            (entry.command["method"], entry.command.get("params"))
            for entry in self._replay
        ]
        if pending is not None:
            commands.append((pending["method"], pending.get("params")))
        return tuple(commands)

    async def _replay_history(self) -> None:
        assert self._upstream is not None
        assert self._upstream_messages is not None
        for entry in self._replay:
            translated = self._rewrite(entry.command, self._forward_ids)
            await self._upstream.send(json.dumps(translated, separators=(",", ":")))
            response, events = await self._replay_response(entry.command)
            if "error" in response:
                raise ProviderTransitionError(
                    f"Replay failed for {entry.command['method']}: provider command error"
                )
            if entry.response is not None:
                self._pair_identifiers(entry.response, response)
            actual_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for event in events:
                method = event.get("method")
                if isinstance(method, str):
                    actual_by_method[method].append(event)
            synthetic_offsets: Counter[str] = Counter()
            for synthetic in entry.events:
                method = synthetic.get("method")
                if not isinstance(method, str):
                    continue
                offset = synthetic_offsets[method]
                candidates = actual_by_method[method]
                if offset < len(candidates):
                    self._pair_identifiers(synthetic, candidates[offset])
                    synthetic_offsets[method] += 1

    async def _replay_response(
        self,
        command: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert self._upstream_messages is not None
        response = None
        events: list[dict[str, Any]] = []
        method = command["method"]
        async with asyncio.timeout(
            self._settings.provider_transition_replay_timeout_seconds
        ):
            while True:
                raw = await anext(self._upstream_messages)
                value = json.loads(raw)
                if value.get("id") == command["id"]:
                    response = value
                elif isinstance(value.get("method"), str):
                    events.append(value)
                if response is not None and self._replay_boundary_reached(method, events):
                    return response, events

    @staticmethod
    def _replay_boundary_reached(method: str, events: list[dict[str, Any]]) -> bool:
        event_methods = {event.get("method") for event in events}
        if method == "Target.createTarget":
            return "Target.attachedToTarget" in event_methods
        if method == "Runtime.enable":
            return "Runtime.executionContextCreated" in event_methods
        if method == "Page.navigate":
            return "Page.loadEventFired" in event_methods
        return True

    async def _forward(self, command: dict[str, Any]) -> None:
        assert self._upstream is not None
        provider = self._upstream.provider
        if not self._capabilities.supports(
            provider,
            command["method"],
            command.get("params"),
        ):
            await self._observer.command_unsupported(command["id"])
            await self._put(
                self._response(
                    command,
                    error={
                        "code": -32601,
                        "message": (
                            f"{command['method']} is not supported by provider {provider.value}"
                        ),
                    },
                )
            )
            return
        entry = ReplayEntry(command=command)
        self._replay.append(entry)
        self._replay_bytes += len(json.dumps(command, separators=(",", ":")).encode())
        self._forwarded[command["id"]] = entry
        self._forward_event_entry = entry
        translated = self._rewrite(command, self._forward_ids)
        try:
            await self._upstream.send(json.dumps(translated, separators=(",", ":")))
        except Exception:
            self._replay.pop()
            self._replay_bytes -= len(
                json.dumps(command, separators=(",", ":")).encode()
            )
            self._forwarded.pop(command["id"], None)
            self._forward_event_entry = None
            raise

    async def _pump_upstream(self) -> None:
        assert self._upstream_messages is not None
        try:
            async for raw in self._upstream_messages:
                value = json.loads(raw)
                rewritten = self._rewrite(value, self._reverse_ids)
                command_id = rewritten.get("id")
                if isinstance(command_id, int):
                    entry = self._forwarded.pop(command_id, None)
                    if entry is not None:
                        entry.response = rewritten
                        self._forward_event_entry = entry
                elif (
                    isinstance(rewritten.get("method"), str)
                    and self._forward_event_entry is not None
                ):
                    self._forward_event_entry.events.append(rewritten)
                await self._put(rewritten)
        finally:
            if not self._closed:
                await self._messages.put(_CLOSED)

    def _pair_identifiers(self, synthetic: Any, actual: Any, parent: str | None = None) -> None:
        if isinstance(synthetic, dict) and isinstance(actual, dict):
            for key in synthetic.keys() & actual.keys():
                kind = self._identifier_kind(key, parent)
                left = synthetic[key]
                right = actual[key]
                if (
                    kind is not None
                    and isinstance(left, str | int)
                    and isinstance(right, str | int)
                ):
                    self._forward_ids[kind][left] = right
                    self._reverse_ids[kind][right] = left
                else:
                    self._pair_identifiers(left, right, key)
        elif isinstance(synthetic, list) and isinstance(actual, list):
            for left, right in zip(synthetic, actual, strict=False):
                self._pair_identifiers(left, right, parent)

    def _rewrite(
        self,
        value: Any,
        mappings: dict[str, dict[object, object]],
        parent: str | None = None,
    ) -> Any:
        if isinstance(value, dict):
            rewritten = {}
            for key, item in value.items():
                kind = self._identifier_kind(key, parent)
                if kind is not None and item in mappings.get(kind, {}):
                    rewritten[key] = mappings[kind][item]
                else:
                    rewritten[key] = self._rewrite(item, mappings, key)
            return rewritten
        if isinstance(value, list):
            return [self._rewrite(item, mappings, parent) for item in value]
        return value

    @staticmethod
    def _identifier_kind(key: str, parent: str | None) -> str | None:
        direct = {
            "browserContextId": "browser_context",
            "targetId": "target",
            "sessionId": "session",
            "frameId": "frame",
            "loaderId": "loader",
            "executionContextId": "execution",
            "contextId": "execution",
            "objectId": "object",
            "windowId": "window",
        }
        if key in direct:
            return direct[key]
        if key == "id" and parent == "frame":
            return "frame"
        if key == "id" and parent == "context":
            return "execution"
        return None

    async def _emit_loaded_document(
        self,
        session_id: str | None,
        request_id: str,
        encoded_length: int,
    ) -> None:
        timestamp = time.monotonic()
        await self._event("Runtime.executionContextsCleared", {}, session_id)
        await self._event(
            "Page.frameNavigated",
            {"frame": self._frame(), "type": "Navigation"},
            session_id,
        )
        self._execution_context_id += 2
        self._utility_context_id = self._execution_context_id + 1
        await self._emit_execution_context(
            session_id,
            self._execution_context_id,
            "",
            True,
        )
        await self._emit_execution_context(
            session_id,
            self._utility_context_id,
            self._utility_world_name,
            False,
        )
        await self._event(
            "Network.loadingFinished",
            {
                "requestId": request_id,
                "timestamp": timestamp,
                "encodedDataLength": encoded_length,
            },
            session_id,
        )
        await self._event("Page.domContentEventFired", {"timestamp": timestamp}, session_id)
        await self._event(
            "Page.lifecycleEvent",
            {
                "frameId": self._frame_id,
                "loaderId": self._loader_id,
                "name": "DOMContentLoaded",
                "timestamp": timestamp,
            },
            session_id,
        )
        await self._event("Page.loadEventFired", {"timestamp": timestamp}, session_id)
        await self._event(
            "Page.lifecycleEvent",
            {
                "frameId": self._frame_id,
                "loaderId": self._loader_id,
                "name": "load",
                "timestamp": timestamp,
            },
            session_id,
        )
        await self._event("Page.frameStoppedLoading", {"frameId": self._frame_id}, session_id)

    async def _emit_execution_context(
        self,
        session_id: str | None,
        context_id: int,
        name: str,
        is_default: bool,
    ) -> None:
        await self._event(
            "Runtime.executionContextCreated",
            {
                "context": {
                    "id": context_id,
                    "origin": self._frame()["securityOrigin"],
                    "name": name,
                    "uniqueId": f"harbor-{self._target_id}-{context_id}",
                    "auxData": {
                        "isDefault": is_default,
                        "type": "default" if is_default else "isolated",
                        "frameId": self._frame_id,
                    },
                }
            },
            session_id,
        )

    async def _event(
        self,
        method: str,
        params: dict[str, Any],
        session_id: str | None,
    ) -> None:
        event = {"method": method, "params": params, **self._session_field(session_id)}
        if self._current_entry is not None:
            self._current_entry.events.append(event)
        await self._put(event)

    async def _emit_transition(
        self,
        from_provider: str,
        to_provider: ProviderName,
        trigger_method: str,
    ) -> None:
        assert self._attempt is not None
        assert self._history is not None
        await self._history.record_transition(
            self._session.session_id,
            self._attempt.attempt.attempt_id,
            from_provider=from_provider,
            to_provider=to_provider,
            trigger_method=trigger_method,
        )

    async def _put(self, message: dict[str, Any]) -> None:
        await self._messages.put(json.dumps(message, separators=(",", ":")))

    def _response(
        self,
        command: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {"id": command["id"]}
        if error is not None:
            response["error"] = error
        else:
            response["result"] = result or {}
        session_id = command.get("sessionId")
        if isinstance(session_id, str):
            response["sessionId"] = session_id
        return response

    def _settings_for(
        self,
        provider: ProviderName,
        source: SettingSource,
    ) -> ResolvedSessionSettings:
        sources = dict(self._resolved.sources)
        sources["harbor.provider.slug"] = source
        return ResolvedSessionSettings(
            provider=ProviderSettingSchema(slug=provider),
            session=self._resolved.session,
            sources=sources,
        )

    def _would_exceed_replay_budget(self, message: str) -> bool:
        return (
            len(self._replay) + 1
            > self._settings.provider_transition_replay_max_commands
            or self._replay_bytes + len(message.encode())
            > self._settings.provider_transition_replay_max_bytes
        )


    def _is_utility_evaluation(self, params: dict[str, Any]) -> bool:
        expression = params.get("expression")
        if not isinstance(expression, str):
            return False
        return (
            hashlib.sha256(expression.encode())
            .hexdigest()
            .startswith(_UTILITY_EXPRESSION_SHA256_PREFIX)
        )

    def _is_content_call(self, params: dict[str, Any]) -> bool:
        object_id = params.get("objectId")
        arguments = params.get("arguments")
        if object_id not in self._utility_objects or not isinstance(arguments, list):
            return False
        if params.get("functionDeclaration") != _UTILITY_CALL or len(arguments) != 6:
            return False
        return (
            arguments[0] == {"objectId": object_id}
            and arguments[1] == {"value": True}
            and arguments[2] == {"value": True}
            and arguments[3] == {"value": _CONTENT_EXPRESSION}
            and arguments[4] == {"value": 1}
            and arguments[5] == {"value": {"v": "undefined"}}
            and params.get("returnByValue") is True
            and params.get("awaitPromise") is True
        )

    def _target_info(self) -> dict[str, Any]:
        return {
            "targetId": self._target_id,
            "type": "page",
            "title": "",
            "url": self._url,
            "attached": True,
            "canAccessOpener": False,
            "browserContextId": self._context_id,
        }

    def _frame(self) -> dict[str, Any]:
        parsed = urlsplit(self._url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else "://"
        return {
            "id": self._frame_id,
            "loaderId": self._loader_id,
            "url": self._url,
            "domainAndRegistry": parsed.hostname or "",
            "securityOrigin": origin,
            "mimeType": "text/html",
            "secureContextType": "Secure" if parsed.scheme == "https" else "InsecureScheme",
        }

    @staticmethod
    def _session_field(session_id: str | None) -> dict[str, str]:
        return {"sessionId": session_id} if isinstance(session_id, str) else {}

    @staticmethod
    def _network_timing() -> dict[str, float | int]:
        return {
            "requestTime": time.monotonic(),
            "proxyStart": -1,
            "proxyEnd": -1,
            "dnsStart": -1,
            "dnsEnd": -1,
            "connectStart": -1,
            "connectEnd": -1,
            "sslStart": -1,
            "sslEnd": -1,
            "workerStart": -1,
            "workerReady": -1,
            "workerFetchStart": -1,
            "workerRespondWithSettled": -1,
            "sendStart": 0,
            "sendEnd": 0,
            "pushStart": 0,
            "pushEnd": 0,
            "receiveHeadersStart": 0,
            "receiveHeadersEnd": 0,
        }


class HttpCdpSession(ProviderTransitionSession):
    """The explicit HTTP provider; it never changes provider automatically."""

    provider = ProviderName.HTTP

    def __init__(
        self,
        session: HarborSession,
        resolved: ResolvedSessionSettings,
        capabilities: CapabilityRegistry,
        settings: Settings,
    ) -> None:
        super().__init__(
            session,
            resolved,
            None,
            capabilities,
            None,
            None,
            settings,
            None,
            automatic=False,
        )
