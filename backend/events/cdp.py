import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from urllib.parse import urlsplit
from uuid import UUID

from backend.events.contracts import SessionEvent
from backend.events.normalization import filter_headers, normalize_domain, sanitize_url
from backend.events.publisher import EventPublisher
from backend.events.registry import (
    COMMAND_SUMMARY_METHOD_LIMIT,
    OTHER_COMMAND_METHOD,
    EventType,
)
from backend.metrics.instrumentation import observe_command
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    sequence: int
    method: str
    domain: str | None
    started_at: float
    forwarded_at: float | None
    captures_content: bool


@dataclass(slots=True)
class _CommandUsage:
    count: int = 0
    failed_count: int = 0
    interrupted_count: int = 0
    duration_ms: int = 0
    provider_latency_ms: int = 0
    harbor_queue_ms: int = 0


@dataclass(slots=True)
class _AttemptPhaseUsage:
    started_at: float
    first_command_at: float | None = None
    last_command_at: float | None = None
    active_started_at: float | None = None
    active_command_count: int = 0
    command_active_seconds: float = 0
    transition_replay_ms: int = 0
    provider_bootstrap_ms: int = 0
    provider_close_ms: int = 0

    def command_started(self, observed_at: float) -> None:
        if self.first_command_at is None:
            self.first_command_at = observed_at
        if self.active_command_count == 0:
            self.active_started_at = observed_at
        self.active_command_count += 1

    def command_finished(self, observed_at: float) -> None:
        if self.active_command_count <= 0:
            return
        self.active_command_count -= 1
        self.last_command_at = observed_at
        if self.active_command_count == 0 and self.active_started_at is not None:
            self.command_active_seconds += max(0, observed_at - self.active_started_at)
            self.active_started_at = None

    def snapshot(self, observed_at: float) -> dict[str, object]:
        observed_ms = max(0, round((observed_at - self.started_at) * 1000))
        active_seconds = self.command_active_seconds
        if self.active_command_count and self.active_started_at is not None:
            active_seconds += max(0, observed_at - self.active_started_at)
        command_active_ms = min(observed_ms, max(0, round(active_seconds * 1000)))
        return {
            "measurement_version": 1,
            "observed_session_ms": observed_ms,
            "command_active_ms": command_active_ms,
            "no_command_in_flight_ms": max(0, observed_ms - command_active_ms),
            "pre_first_command_ms": (
                max(0, round((self.first_command_at - self.started_at) * 1000))
                if self.first_command_at is not None
                else None
            ),
            "post_last_command_ms": (
                max(0, round((observed_at - self.last_command_at) * 1000))
                if self.last_command_at is not None
                else None
            ),
            "transition_replay_ms": self.transition_replay_ms,
            "provider_bootstrap_ms": self.provider_bootstrap_ms,
            "provider_close_ms": self.provider_close_ms,
        }


_ATTEMPT_PHASE_NAMES = frozenset(
    {
        "transition_replay_ms",
        "provider_bootstrap_ms",
        "provider_close_ms",
    }
)


_CONTENT_EXPRESSION = """() => {
        let retVal = "";
        if (document.doctype)
          retVal = new XMLSerializer().serializeToString(document.doctype);
        if (document.documentElement)
          retVal += document.documentElement.outerHTML;
        return retVal;
      }"""


def _captures_page_content(method: str, params: dict) -> bool:
    if method != "Runtime.callFunctionOn":
        return False
    arguments = params.get("arguments")
    return (
        isinstance(arguments, list)
        and len(arguments) > 3
        and arguments[3] == {"value": _CONTENT_EXPRESSION}
        and params.get("returnByValue") is True
    )


class CdpEventObserver:
    def __init__(
        self,
        session_id: UUID,
        attempt_id: UUID | None,
        provider: ProviderName | None,
        publisher: EventPublisher,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_id = session_id
        self._attempt_id = attempt_id
        self._provider = provider
        self._publisher = publisher
        self._clock = clock
        self._pending: dict[tuple[str | None, int], _PendingCommand] = {}
        self._domain: str | None = None
        self._command_sequence = 0
        self._command_usage: dict[
            tuple[ProviderName, UUID],
            dict[str, _CommandUsage],
        ] = {}
        self._attempt_phases: dict[
            tuple[ProviderName, UUID],
            _AttemptPhaseUsage,
        ] = {}
        self._pending_phase_keys: dict[
            tuple[str | None, int],
            tuple[ProviderName, UUID],
        ] = {}
        if provider is not None and attempt_id is not None:
            self.start_attempt_phase(provider, attempt_id)

    def bind_attempt(self, provider: ProviderName, attempt_id: UUID | None) -> None:
        previous = self._attempt_key()
        self._provider = provider
        self._attempt_id = attempt_id
        current = self._attempt_key()
        if current is None:
            return
        observed_at = self._clock()
        self._attempt_phases.setdefault(current, _AttemptPhaseUsage(observed_at))
        if previous == current:
            return
        for pending_key, phase_key in list(self._pending_phase_keys.items()):
            if phase_key != previous:
                continue
            self._finish_phase_command(phase_key, observed_at)
            self._start_phase_command(current, observed_at)
            self._pending_phase_keys[pending_key] = current

    def start_attempt_phase(self, provider: ProviderName, attempt_id: UUID) -> None:
        self._attempt_phases.setdefault(
            (provider, attempt_id),
            _AttemptPhaseUsage(self._clock()),
        )

    def record_attempt_phase(
        self,
        attempt_id: UUID,
        phase: str,
        duration_ms: int,
    ) -> None:
        if phase not in _ATTEMPT_PHASE_NAMES:
            raise ValueError(f"Unknown attempt phase {phase}")
        match = self._phase_match(attempt_id)
        if match is None:
            return
        usage = self._attempt_phases[match]
        setattr(usage, phase, getattr(usage, phase) + max(0, duration_ms))

    def record_attempt_phases(
        self,
        attempt_id: UUID,
        phases: object,
    ) -> None:
        if not isinstance(phases, dict):
            return
        for phase, duration_ms in phases.items():
            if (
                isinstance(phase, str)
                and phase in _ATTEMPT_PHASE_NAMES
                and isinstance(duration_ms, int)
            ):
                self.record_attempt_phase(attempt_id, phase, duration_ms)

    def phase_summary(self, attempt_id: UUID) -> dict[str, object] | None:
        match = self._phase_match(attempt_id)
        if match is None:
            return None
        return self._attempt_phases[match].snapshot(self._clock())

    async def command_received(self, command: dict) -> None:
        command_id = command["id"]
        session_id = command.get("sessionId")
        if not isinstance(session_id, str):
            session_id = None
        method = command["method"]
        params = command.get("params")
        if not isinstance(params, dict):
            params = {}
        domain = self._domain
        if method == "Page.navigate" and isinstance(params.get("url"), str):
            raw_url = params["url"]
            url = sanitize_url(raw_url)
            domain = normalize_domain(raw_url)
            if url is not None:
                parsed = urlsplit(raw_url)
                await self._emit(
                    EventType.NAVIGATION_REQUESTED,
                    {
                        "url": url,
                        "probe_safe": (
                            parsed.scheme in {"http", "https"}
                            and parsed.hostname is not None
                            and parsed.username is None
                            and parsed.password is None
                            and not parsed.query
                        ),
                    },
                )
        self._command_sequence += 1
        self._pending[(session_id, command_id)] = _PendingCommand(
            self._command_sequence,
            method,
            domain,
            self._clock(),
            None,
            _captures_page_content(method, params),
        )

    def command_forwarded(self, command: dict) -> None:
        command_id = command.get("id")
        if not isinstance(command_id, int):
            return
        session_id = command.get("sessionId")
        if not isinstance(session_id, str):
            session_id = None
        key = (session_id, command_id)
        pending = self._pending.get(key)
        if pending is not None:
            observed_at = self._clock()
            self._pending[key] = replace(pending, forwarded_at=observed_at)
            phase_key = self._attempt_key()
            if phase_key is not None and key not in self._pending_phase_keys:
                self._start_phase_command(phase_key, observed_at)
                self._pending_phase_keys[key] = phase_key

    async def command_unsupported(self, command_id: int) -> None:
        await self._finish_command(
            command_id,
            EventType.COMMAND_FAILED,
            reason="unsupported_command",
            cdp_error_code=-32601,
        )

    async def upstream_message(self, message: str) -> None:
        try:
            value = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(value, dict):
            return
        try:
            await self._observe_upstream(value)
        except Exception:
            # Provider evidence must never disrupt the protocol relay.
            return

    async def _observe_upstream(self, value: dict) -> None:
        if isinstance(value.get("id"), int):
            session_id = value.get("sessionId")
            if not isinstance(session_id, str):
                session_id = None
            error = value.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                await self._finish_command(
                    value["id"],
                    EventType.COMMAND_FAILED,
                    session_id=session_id,
                    reason="provider_command_error",
                    cdp_error_code=code if isinstance(code, int) else None,
                )
            else:
                await self._observe_content(value["id"], value)
                await self._finish_command(
                    value["id"],
                    None,
                    session_id=session_id,
                )
            return

        method = value.get("method")
        params = value.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "Network.requestWillBeSent" and params.get("type") == "Document":
            request = params.get("request")
            if not isinstance(request, dict):
                request = {}
            url = sanitize_url(request.get("url", ""))
            redirect = params.get("redirectResponse")
            if not isinstance(redirect, dict):
                redirect = {}
            previous_url = sanitize_url(redirect.get("url", ""))
            if previous_url and url:
                await self._emit(
                    EventType.NAVIGATION_REDIRECTED,
                    {"previous_url": previous_url, "url": url},
                )
        elif method == "Network.responseReceived" and params.get("type") == "Document":
            response = params.get("response")
            if not isinstance(response, dict):
                response = {}
            raw_url = response.get("url", "")
            url = sanitize_url(raw_url)
            if url is not None:
                self._domain = normalize_domain(raw_url)
                payload = {
                    "url": url,
                    "status": (
                        int(response["status"])
                        if isinstance(response.get("status"), int | float)
                        else None
                    ),
                    "mime_type": response.get("mimeType"),
                    "resource_type": "Document",
                    "selected_headers": filter_headers(
                        response["headers"] if isinstance(response.get("headers"), dict) else {}
                    ),
                }
                await self._emit(
                    EventType.NAVIGATION_RESPONSE,
                    {key: item for key, item in payload.items() if item is not None},
                )
        elif method == "Network.loadingFailed" and params.get("type") == "Document":
            await self._emit(
                EventType.NAVIGATION_FAILED,
                {"error_type": "network_error"},
            )
        elif method in {"Inspector.targetCrashed", "Page.crashed"}:
            await self._emit(EventType.PAGE_CRASHED, {})
        elif method == "Runtime.consoleAPICalled":
            values = params.get("args")
            text = (
                " ".join(
                    str(item.get("value", item.get("description", "")))
                    for item in values
                    if isinstance(item, dict)
                )
                if isinstance(values, list)
                else ""
            )
            await self._console(
                EventType.CONSOLE_MESSAGE,
                str(params.get("type", "log")),
                "runtime",
                text,
            )
        elif method == "Log.entryAdded":
            entry = params.get("entry")
            if isinstance(entry, dict):
                await self._console(
                    EventType.CONSOLE_MESSAGE,
                    str(entry.get("level", "info")),
                    str(entry.get("source", "log")),
                    str(entry.get("text", "")),
                )
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails")
            if isinstance(details, dict):
                exception = details.get("exception")
                description = (
                    exception.get("description", "")
                    if isinstance(exception, dict)
                    else details.get("text", "")
                )
                await self._console(
                    EventType.JAVASCRIPT_EXCEPTION,
                    "error",
                    "runtime",
                    str(description),
                )

    async def _observe_content(self, command_id: int, value: dict) -> None:
        pending = self._pending_for_id(command_id)
        if pending is None or not pending.captures_content:
            return
        outer = value.get("result")
        result = outer.get("result") if isinstance(outer, dict) else None
        content = result.get("value") if isinstance(result, dict) else None
        if not isinstance(content, str):
            return
        await self._emit(
            EventType.PAGE_CONTENT_OBSERVED,
            {
                "content_length": len(content.encode()),
            },
        )

    async def _console(
        self,
        event_type: EventType,
        level: str,
        source: str,
        text: str,
    ) -> None:
        await self._emit(
            event_type,
            {
                "level": level[:16],
                "source": source[:32],
                "message_fingerprint": hashlib.sha256(text.encode()).hexdigest(),
            },
        )

    async def provider_disconnected(self, reason: str) -> None:
        await self._emit(EventType.PROVIDER_DISCONNECTED, {"reason": reason})

    async def interrupt_pending(self, reason: str) -> None:
        for session_id, command_id in list(self._pending):
            await self._finish_command(
                command_id,
                EventType.COMMAND_INTERRUPTED,
                session_id=session_id,
                reason=reason,
            )

    async def _finish_command(
        self,
        command_id: int,
        event_type: EventType | None,
        *,
        session_id: str | None = None,
        reason: str | None = None,
        cdp_error_code: int | None = None,
    ) -> None:
        pending = self._pending.pop((session_id, command_id), None)
        if pending is None and session_id is None:
            pending = self._pop_pending_for_id(command_id)
        if pending is None:
            return
        finished_at = self._clock()
        phase_key = self._pending_phase_keys.pop((session_id, command_id), None)
        if phase_key is None and session_id is None:
            phase_key = self._pop_pending_phase_for_id(command_id)
        if phase_key is not None:
            self._finish_phase_command(phase_key, finished_at)
        duration_ms = round((finished_at - pending.started_at) * 1000)
        provider_latency_ms = (
            round((finished_at - pending.forwarded_at) * 1000)
            if pending.forwarded_at is not None
            else 0
        )
        harbor_queue_ms = (
            round((pending.forwarded_at - pending.started_at) * 1000)
            if pending.forwarded_at is not None
            else 0
        )
        outcome = (
            "failed"
            if event_type is EventType.COMMAND_FAILED
            else "interrupted"
            if event_type is EventType.COMMAND_INTERRUPTED
            else "succeeded"
        )
        if self._provider is not None:
            observe_command(
                self._provider,
                pending.method,
                outcome,
                duration_ms,
            )
        if self._provider is not None and self._attempt_id is not None:
            methods = self._command_usage.setdefault(
                (self._provider, self._attempt_id),
                {},
            )
            retained_method = pending.method[:128]
            if retained_method not in methods and len(methods) >= COMMAND_SUMMARY_METHOD_LIMIT - 1:
                retained_method = OTHER_COMMAND_METHOD
            usage = methods.setdefault(retained_method, _CommandUsage())
            usage.count += 1
            usage.failed_count += int(outcome == "failed")
            usage.interrupted_count += int(outcome == "interrupted")
            usage.duration_ms += duration_ms
            usage.provider_latency_ms += provider_latency_ms
            usage.harbor_queue_ms += harbor_queue_ms
        if event_type is None:
            return
        payload = {
            "command_id": command_id,
            "command_sequence": pending.sequence,
            "method": pending.method[:128],
            "domain": pending.domain,
            "duration_ms": duration_ms,
            "provider_latency_ms": provider_latency_ms,
            "harbor_queue_ms": harbor_queue_ms,
            "reason": reason,
            "cdp_error_code": cdp_error_code,
        }
        await self._emit(
            event_type,
            {key: item for key, item in payload.items() if item is not None},
        )

    async def flush_command_summary(self, attempt_id: UUID) -> None:
        matches = [
            (key, methods) for key, methods in self._command_usage.items() if key[1] == attempt_id
        ]
        for (provider, matched_attempt_id), methods in matches:
            del self._command_usage[(provider, matched_attempt_id)]
            await self._emit_for_attempt(
                EventType.COMMAND_SUMMARY,
                provider,
                matched_attempt_id,
                {
                    "methods": {
                        method: {
                            "count": usage.count,
                            "failed_count": usage.failed_count,
                            "interrupted_count": usage.interrupted_count,
                            "duration_ms": usage.duration_ms,
                            "provider_latency_ms": usage.provider_latency_ms,
                            "harbor_queue_ms": usage.harbor_queue_ms,
                        }
                        for method, usage in methods.items()
                    }
                },
            )
        phase_matches = [key for key in self._attempt_phases if key[1] == attempt_id]
        for key in phase_matches:
            del self._attempt_phases[key]

    def command_summary(self, attempt_id: UUID) -> dict[str, object] | None:
        matches = [
            methods
            for (_, matched_attempt_id), methods in self._command_usage.items()
            if matched_attempt_id == attempt_id
        ]
        if not matches:
            return None
        methods = matches[0]
        return {
            "methods": {
                method: {
                    "count": usage.count,
                    "failed_count": usage.failed_count,
                    "interrupted_count": usage.interrupted_count,
                    "duration_ms": usage.duration_ms,
                    "provider_latency_ms": usage.provider_latency_ms,
                    "harbor_queue_ms": usage.harbor_queue_ms,
                }
                for method, usage in methods.items()
            }
        }

    async def flush_command_summaries(self) -> None:
        attempt_ids = {
            attempt_id
            for _, attempt_id in (*self._command_usage, *self._attempt_phases)
        }
        for attempt_id in attempt_ids:
            await self.flush_command_summary(attempt_id)

    def _attempt_key(self) -> tuple[ProviderName, UUID] | None:
        if self._provider is None or self._attempt_id is None:
            return None
        return self._provider, self._attempt_id

    def _phase_match(self, attempt_id: UUID) -> tuple[ProviderName, UUID] | None:
        matches = [key for key in self._attempt_phases if key[1] == attempt_id]
        return matches[0] if len(matches) == 1 else None

    def _start_phase_command(
        self,
        phase_key: tuple[ProviderName, UUID],
        observed_at: float,
    ) -> None:
        usage = self._attempt_phases.setdefault(
            phase_key,
            _AttemptPhaseUsage(observed_at),
        )
        usage.command_started(observed_at)

    def _finish_phase_command(
        self,
        phase_key: tuple[ProviderName, UUID],
        observed_at: float,
    ) -> None:
        usage = self._attempt_phases.get(phase_key)
        if usage is not None:
            usage.command_finished(observed_at)

    def _pop_pending_phase_for_id(
        self,
        command_id: int,
    ) -> tuple[ProviderName, UUID] | None:
        matches = [key for key in self._pending_phase_keys if key[1] == command_id]
        if len(matches) != 1:
            return None
        return self._pending_phase_keys.pop(matches[0])

    def _pending_for_id(self, command_id: int) -> _PendingCommand | None:
        matches = [
            pending
            for (_, pending_id), pending in self._pending.items()
            if pending_id == command_id
        ]
        return matches[0] if len(matches) == 1 else None

    def _pop_pending_for_id(self, command_id: int) -> _PendingCommand | None:
        matches = [key for key in self._pending if key[1] == command_id]
        return self._pending.pop(matches[0]) if len(matches) == 1 else None

    async def _emit(self, event_type: EventType, payload: dict) -> None:
        await self._emit_for_attempt(
            event_type,
            self._provider,
            self._attempt_id,
            payload,
        )

    async def _emit_for_attempt(
        self,
        event_type: EventType,
        provider: ProviderName | None,
        attempt_id: UUID | None,
        payload: dict,
    ) -> None:
        event = SessionEvent.create(
            event_type,
            self._session_id,
            provider=provider,
            attempt_id=attempt_id,
            payload=payload,
        )
        try:
            await self._publisher.publish(event)
        except Exception:
            return
