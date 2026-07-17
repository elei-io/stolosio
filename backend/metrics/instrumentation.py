import logging

from backend.events import EventType, SessionEvent
from backend.events.publisher import EventPublisher
from backend.metrics.definitions import (
    COMMAND_DURATION,
    COMMANDS,
    EVENT_PUBLICATION_FAILURES,
    PROVIDER_FAILURES,
    PROVIDER_TRANSITIONS,
    SESSION_ACQUISITION_DURATION,
    SESSION_ACQUISITIONS,
    SESSIONS_COMPLETED,
)
from backend.proxy.capabilities.manifests import PROVIDER_METHODS

logger = logging.getLogger(__name__)

_KNOWN_METHODS = frozenset().union(*PROVIDER_METHODS.values())
_KNOWN_REASONS = frozenset(
    {
        "client_disconnected",
        "provider_acquisition_timeout",
        "provider_connection_lost",
        "provider_unavailable",
        "provider_queue_full",
        "provider_queue_timeout",
        "gateway_capacity_full",
        "session_lease_lost",
        "session_lease_expired",
        "unsupported_command",
        "provider_command_error",
        "session_ended",
    }
)


def metric_method(method: object) -> str:
    return method if isinstance(method, str) and method in _KNOWN_METHODS else "other"


def metric_reason(reason: object) -> str:
    return reason if isinstance(reason, str) and reason in _KNOWN_REASONS else "other"


def transition_trigger(trigger: object) -> str:
    if trigger == "replay_budget":
        return "replay_budget"
    if trigger in {"http_transport_failure", "http_response_too_large"}:
        return "http_safety"
    if isinstance(trigger, str) and trigger in _KNOWN_METHODS:
        return "new_requirement"
    return "other"


def observe_published_event(event: SessionEvent) -> None:
    event_type = EventType(event.event_type)
    duration = event.payload.get("duration_ms")

    if event_type in {EventType.SESSION_CLOSED, EventType.SESSION_FAILED}:
        outcome = "closed" if event_type is EventType.SESSION_CLOSED else "failed"
        SESSIONS_COMPLETED.labels(outcome).inc()
        return
    if event_type is EventType.EXECUTION_TRANSITIONED:
        PROVIDER_TRANSITIONS.labels(
            event.payload["from_provider"],
            event.payload["to_provider"],
            transition_trigger(event.payload.get("trigger_method")),
        ).inc()
        return
    if event.provider is None:
        return
    provider = event.provider.value

    if event_type is EventType.ATTEMPT_CONNECTED:
        SESSION_ACQUISITIONS.labels(provider, "connected").inc()
        if isinstance(duration, int):
            SESSION_ACQUISITION_DURATION.labels(provider).observe(duration / 1000)
    elif event_type is EventType.ATTEMPT_FAILED:
        SESSION_ACQUISITIONS.labels(provider, "failed").inc()
        PROVIDER_FAILURES.labels(provider, metric_reason(event.payload.get("reason"))).inc()
        if isinstance(duration, int):
            SESSION_ACQUISITION_DURATION.labels(provider).observe(duration / 1000)
    elif event_type in {
        EventType.COMMAND_SUCCEEDED,
        EventType.COMMAND_FAILED,
        EventType.COMMAND_INTERRUPTED,
    }:
        outcome = event_type.value.removeprefix("command.")
        method = metric_method(event.payload.get("method"))
        COMMANDS.labels(provider, method, outcome).inc()
        if isinstance(duration, int):
            COMMAND_DURATION.labels(provider, method, outcome).observe(duration / 1000)


class InstrumentedEventPublisher:
    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def publish(self, event: SessionEvent) -> None:
        try:
            await self._publisher.publish(event)
        except Exception:
            EVENT_PUBLICATION_FAILURES.labels(event.event_type).inc()
            logger.warning(
                "Event publication failed type=%s session_id=%s",
                event.event_type,
                event.session_id,
                exc_info=True,
            )
            raise
        observe_published_event(event)
