from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    SESSION_REQUESTED = "session.requested"
    SESSION_ADMITTED = "session.admitted"
    SESSION_OPEN = "session.open"
    SESSION_CLOSING = "session.closing"
    SESSION_CLOSED = "session.closed"
    SESSION_FAILED = "session.failed"
    ATTEMPT_STARTED = "attempt.started"
    ATTEMPT_QUEUED = "attempt.queued"
    ATTEMPT_ACQUIRING = "attempt.acquiring"
    ATTEMPT_CONNECTED = "attempt.connected"
    ATTEMPT_FAILED = "attempt.failed"
    ATTEMPT_CLOSED = "attempt.closed"
    COMMAND_RECEIVED = "command.received"
    COMMAND_SUCCEEDED = "command.succeeded"
    COMMAND_FAILED = "command.failed"
    COMMAND_INTERRUPTED = "command.interrupted"
    NAVIGATION_REQUESTED = "navigation.requested"
    NAVIGATION_REDIRECTED = "navigation.redirected"
    NAVIGATION_RESPONSE = "navigation.response"
    NAVIGATION_FAILED = "navigation.failed"
    PAGE_DOM_CONTENT_LOADED = "page.dom_content_loaded"
    PAGE_LOADED = "page.loaded"
    PAGE_CONTENT_OBSERVED = "page.content_observed"
    PAGE_CRASHED = "page.crashed"
    CONSOLE_MESSAGE = "console.message"
    JAVASCRIPT_EXCEPTION = "javascript.exception"
    PROVIDER_DISCONNECTED = "provider.disconnected"
    EXECUTION_PROMOTED = "execution.promoted"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LifecyclePayload(_Payload):
    requested_settings: dict[str, Any] | None = None
    resolved_settings: dict[str, Any] | None = None
    setting_sources: dict[str, str] | None = None
    reason: str | None = Field(default=None, max_length=64)


class AttemptPayload(_Payload):
    duration_ms: int | None = None
    cost_units: int | None = None
    reason: str | None = Field(default=None, max_length=64)
    resolved_settings: dict[str, Any] | None = None
    setting_sources: dict[str, str] | None = None


class CommandPayload(_Payload):
    command_id: int
    method: str = Field(max_length=128)
    domain: str | None = Field(default=None, max_length=253)
    duration_ms: int | None = None
    reason: str | None = Field(default=None, max_length=64)
    cdp_error_code: int | None = None


class ObservationPayload(_Payload):
    url: str | None = None
    previous_url: str | None = None
    status: int | None = None
    mime_type: str | None = None
    resource_type: str | None = None
    selected_headers: dict[str, str | list[str]] | None = None
    error_type: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = None
    probe_safe: bool | None = None
    content_fingerprint: str | None = Field(default=None, max_length=64)
    content_length: int | None = None
    level: str | None = Field(default=None, max_length=16)
    source: str | None = Field(default=None, max_length=32)
    message_fingerprint: str | None = Field(default=None, max_length=64)


class PromotionPayload(_Payload):
    from_provider: str = Field(max_length=32)
    to_provider: str = Field(max_length=32)
    trigger_method: str = Field(max_length=128)


_LIFECYCLE = {
    EventType.SESSION_REQUESTED,
    EventType.SESSION_ADMITTED,
    EventType.SESSION_OPEN,
    EventType.SESSION_CLOSING,
    EventType.SESSION_CLOSED,
    EventType.SESSION_FAILED,
}
_ATTEMPTS = {
    EventType.ATTEMPT_STARTED,
    EventType.ATTEMPT_QUEUED,
    EventType.ATTEMPT_ACQUIRING,
    EventType.ATTEMPT_CONNECTED,
    EventType.ATTEMPT_FAILED,
    EventType.ATTEMPT_CLOSED,
}
_COMMANDS = {
    EventType.COMMAND_RECEIVED,
    EventType.COMMAND_SUCCEEDED,
    EventType.COMMAND_FAILED,
    EventType.COMMAND_INTERRUPTED,
}
_OBSERVATIONS = set(EventType) - _LIFECYCLE - _ATTEMPTS - _COMMANDS

_MODELS: dict[EventType, type[_Payload]] = {
    **dict.fromkeys(_LIFECYCLE, LifecyclePayload),
    **dict.fromkeys(_ATTEMPTS, AttemptPayload),
    **dict.fromkeys(_COMMANDS, CommandPayload),
    **dict.fromkeys(_OBSERVATIONS, ObservationPayload),
    EventType.EXECUTION_PROMOTED: PromotionPayload,
}


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        registered = EventType(event_type)
    except ValueError as error:
        raise ValueError(f"Unknown event type {event_type}") from error
    validated = _MODELS[registered].model_validate(payload)
    return validated.model_dump(mode="json", exclude_none=True)
