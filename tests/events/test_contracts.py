import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.events import EventType, SessionEvent
from backend.proxy.contracts import ProviderName


def test_event_round_trip_is_stable_and_validated() -> None:
    event = SessionEvent.create(
        EventType.COMMAND_SUMMARY,
        uuid4(),
        provider=ProviderName.BROWSERLESS,
        payload={
            "methods": {
                "Page.navigate": {
                    "count": 1,
                    "failed_count": 0,
                    "duration_ms": 25,
                    "provider_latency_ms": 21,
                    "stolosio_queue_ms": 4,
                }
            }
        },
        occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
    )

    decoded = SessionEvent.from_json(event.to_json())

    assert decoded == event
    assert decoded.subject == f"stolosio.v1.events.session.{event.session_id}"


def test_unknown_event_and_payload_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown event type"):
        SessionEvent.create("stolosio.invented", uuid4())

    with pytest.raises(ValueError):
        SessionEvent.create(
            EventType.COMMAND_FAILED,
            uuid4(),
            payload={"command_id": 1, "method": "Page.navigate", "cookie": "secret"},
        )


def test_unknown_envelope_fields_and_versions_are_rejected() -> None:
    event = SessionEvent.create(EventType.SESSION_CLOSED, uuid4())
    raw = json.loads(event.to_json())
    raw["unexpected"] = True
    with pytest.raises(ValueError):
        SessionEvent.from_json(json.dumps(raw))

    raw.pop("unexpected")
    raw["schema_version"] = 99
    with pytest.raises(ValueError, match="Unsupported event schema"):
        SessionEvent.from_json(json.dumps(raw))


def test_transition_event_records_the_factual_transition() -> None:
    event = SessionEvent.create(
        EventType.EXECUTION_TRANSITIONED,
        uuid4(),
        provider=ProviderName.BROWSERLESS,
        attempt_id=uuid4(),
        payload={
            "from_provider": "http",
            "to_provider": "browserless",
            "trigger_method": "Runtime.evaluate",
        },
    )

    assert SessionEvent.from_json(event.to_json()) == event


def test_historic_provider_disconnect_without_reason_remains_readable() -> None:
    event = SessionEvent.create(
        EventType.PROVIDER_DISCONNECTED,
        uuid4(),
        provider=ProviderName.BROWSERLESS,
    )

    assert event.payload == {}
    assert SessionEvent.from_json(event.to_json()) == event


def test_event_contract_bounds_database_fields_and_requires_timezone() -> None:
    with pytest.raises(ValueError):
        SessionEvent.create(
            EventType.COMMAND_SUMMARY,
            uuid4(),
            payload={
                "methods": {
                    "x" * 129: {
                        "count": 1,
                        "failed_count": 0,
                        "duration_ms": 1,
                        "provider_latency_ms": 1,
                        "stolosio_queue_ms": 0,
                    }
                }
            },
        )
    with pytest.raises(ValueError, match="timezone"):
        SessionEvent.create(
            EventType.SESSION_OPEN,
            uuid4(),
            occurred_at=datetime(2026, 7, 16),
        )
