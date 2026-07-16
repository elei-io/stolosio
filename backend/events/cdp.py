import json
import time
from dataclasses import dataclass
from uuid import UUID

from backend.events.contracts import SessionEvent
from backend.events.normalization import filter_headers, normalize_domain, sanitize_url
from backend.events.publisher import EventPublisher
from backend.events.registry import EventType
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class _PendingCommand:
    method: str
    domain: str | None
    started_at: float


class CdpEventObserver:
    def __init__(
        self,
        session_id: UUID,
        attempt_id: UUID,
        provider: ProviderName,
        publisher: EventPublisher,
    ) -> None:
        self._session_id = session_id
        self._attempt_id = attempt_id
        self._provider = provider
        self._publisher = publisher
        self._pending: dict[int, _PendingCommand] = {}
        self._domain: str | None = None

    async def command_received(self, command: dict) -> None:
        command_id = command["id"]
        method = command["method"]
        params = command.get("params")
        if not isinstance(params, dict):
            params = {}
        domain = self._domain
        if method == "Page.navigate" and isinstance(params.get("url"), str):
            url = sanitize_url(params["url"])
            domain = normalize_domain(params["url"])
            if url is not None:
                await self._emit(EventType.NAVIGATION_REQUESTED, {"url": url})
        self._pending[command_id] = _PendingCommand(method, domain, time.monotonic())
        await self._emit(
            EventType.COMMAND_RECEIVED,
            {"command_id": command_id, "method": method, "domain": domain},
        )

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
            error = value.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                await self._finish_command(
                    value["id"],
                    EventType.COMMAND_FAILED,
                    reason="provider_command_error",
                    cdp_error_code=code if isinstance(code, int) else None,
                )
            else:
                await self._finish_command(value["id"], EventType.COMMAND_SUCCEEDED)
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
        elif method == "Page.domContentEventFired":
            await self._emit(EventType.PAGE_DOM_CONTENT_LOADED, {})
        elif method == "Page.loadEventFired":
            await self._emit(EventType.PAGE_LOADED, {})
        elif method in {"Inspector.targetCrashed", "Page.crashed"}:
            await self._emit(EventType.PAGE_CRASHED, {})

    async def provider_disconnected(self) -> None:
        await self._emit(EventType.PROVIDER_DISCONNECTED, {})

    async def interrupt_pending(self, reason: str) -> None:
        for command_id in list(self._pending):
            await self._finish_command(
                command_id,
                EventType.COMMAND_INTERRUPTED,
                reason=reason,
            )

    async def _finish_command(
        self,
        command_id: int,
        event_type: EventType,
        *,
        reason: str | None = None,
        cdp_error_code: int | None = None,
    ) -> None:
        pending = self._pending.pop(command_id, None)
        if pending is None:
            return
        payload = {
            "command_id": command_id,
            "method": pending.method,
            "domain": pending.domain,
            "duration_ms": round((time.monotonic() - pending.started_at) * 1000),
            "reason": reason,
            "cdp_error_code": cdp_error_code,
        }
        await self._emit(
            event_type,
            {key: item for key, item in payload.items() if item is not None},
        )

    async def _emit(self, event_type: EventType, payload: dict) -> None:
        event = SessionEvent.create(
            event_type,
            self._session_id,
            provider=self._provider,
            attempt_id=self._attempt_id,
            payload=payload,
        )
        try:
            await self._publisher.publish(event)
        except Exception:
            return
