from enum import StrEnum
from typing import Annotated, Any, Literal

from pagecapture.api import DecisionCode, Path, Tier
from pydantic import BaseModel, ConfigDict, Field

COMMAND_SUMMARY_METHOD_LIMIT = 256
OTHER_COMMAND_METHOD = "__other__"


class EventType(StrEnum):
    SESSION_OPEN = "session.open"
    SESSION_CLOSED = "session.closed"
    SESSION_FAILED = "session.failed"
    ATTEMPT_CONNECTED = "attempt.connected"
    ATTEMPT_FAILED = "attempt.failed"
    ATTEMPT_CLOSED = "attempt.closed"
    COMMAND_SUMMARY = "command.summary"
    COMMAND_FAILED = "command.failed"
    COMMAND_INTERRUPTED = "command.interrupted"
    NAVIGATION_REQUESTED = "navigation.requested"
    NAVIGATION_REDIRECTED = "navigation.redirected"
    NAVIGATION_RESPONSE = "navigation.response"
    NAVIGATION_FAILED = "navigation.failed"
    PAGE_CONTENT_OBSERVED = "page.content_observed"
    PAGE_CRASHED = "page.crashed"
    CONSOLE_MESSAGE = "console.message"
    JAVASCRIPT_EXCEPTION = "javascript.exception"
    PROVIDER_DISCONNECTED = "provider.disconnected"
    CAPTURE_COMPLETED = "capture.completed"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LifecyclePayload(_Payload):
    reason: str | None = Field(default=None, max_length=64)


class AttemptPayload(_Payload):
    duration_ms: int | None = None
    cost_units: int | None = None
    reason: str | None = Field(default=None, max_length=64)


class CommandPayload(_Payload):
    command_id: int
    command_sequence: int | None = None
    method: str = Field(max_length=128)
    domain: str | None = Field(default=None, max_length=253)
    duration_ms: int | None = None
    provider_latency_ms: int | None = None
    stolosio_queue_ms: int | None = None
    reason: str | None = Field(default=None, max_length=64)
    cdp_error_code: int | None = None


class CommandUsagePayload(_Payload):
    count: int = Field(ge=1)
    failed_count: int = Field(ge=0)
    interrupted_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(ge=0)
    provider_latency_ms: int = Field(ge=0)
    stolosio_queue_ms: int = Field(ge=0)


class CommandSummaryPayload(_Payload):
    methods: dict[
        Annotated[str, Field(max_length=128)],
        CommandUsagePayload,
    ] = Field(max_length=COMMAND_SUMMARY_METHOD_LIMIT)


class ObservationPayload(_Payload):
    url: str | None = None
    previous_url: str | None = None
    status: int | None = None
    mime_type: str | None = None
    resource_type: str | None = None
    selected_headers: dict[str, str | list[str]] | None = None
    error_type: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = None
    content_length: int | None = None
    level: str | None = Field(default=None, max_length=16)
    source: str | None = Field(default=None, max_length=32)
    message_fingerprint: str | None = Field(default=None, max_length=64)


class CaptureAttemptPayload(_Payload):
    path: Path
    tier: Tier
    status_code: int | None = Field(default=None, ge=100, le=599)
    duration_ms: float = Field(ge=0)
    assessment: str | None = Field(default=None, pattern=r"^[a-z_]{1,64}$")
    decision: Literal["accept", "escalate", "fail"]
    reason: DecisionCode
    http_coverage: float | None = Field(default=None, ge=0, le=1)
    http_sufficient: bool | None = None


class CapturePayload(_Payload):
    outcome: Literal["captured", "failed"]
    failure_code: str | None = Field(default=None, max_length=64)
    failure_category: str | None = Field(default=None, max_length=16)
    representation: str | None = Field(default=None, max_length=16)
    tiers: list[Annotated[str, Field(max_length=32)]] = Field(max_length=8)
    duration_ms: int = Field(ge=0)
    browser_seconds: float = Field(ge=0)
    paid: bool
    bytes: int = Field(ge=0)
    attempts: list[CaptureAttemptPayload] = Field(max_length=8)


class ProviderDisconnectPayload(_Payload):
    reason: str | None = Field(default=None, max_length=64)


_LIFECYCLE = {
    EventType.SESSION_OPEN,
    EventType.SESSION_CLOSED,
    EventType.SESSION_FAILED,
}
_ATTEMPTS = {
    EventType.ATTEMPT_CONNECTED,
    EventType.ATTEMPT_FAILED,
    EventType.ATTEMPT_CLOSED,
}
_COMMANDS = {
    EventType.COMMAND_SUMMARY,
    EventType.COMMAND_FAILED,
    EventType.COMMAND_INTERRUPTED,
}
_OBSERVATIONS = set(EventType) - _LIFECYCLE - _ATTEMPTS - _COMMANDS - {EventType.CAPTURE_COMPLETED}

_MODELS: dict[EventType, type[_Payload]] = {
    **dict.fromkeys(_LIFECYCLE, LifecyclePayload),
    **dict.fromkeys(_ATTEMPTS, AttemptPayload),
    **dict.fromkeys(_COMMANDS, CommandPayload),
    **dict.fromkeys(_OBSERVATIONS, ObservationPayload),
    EventType.PROVIDER_DISCONNECTED: ProviderDisconnectPayload,
    EventType.COMMAND_SUMMARY: CommandSummaryPayload,
    EventType.CAPTURE_COMPLETED: CapturePayload,
}


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        registered = EventType(event_type)
    except ValueError as error:
        raise ValueError(f"Unknown event type {event_type}") from error
    validated = _MODELS[registered].model_validate(payload)
    return validated.model_dump(mode="json", exclude_none=True)
