import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from backend.proxy.contracts import ProviderName


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    schema_version: int
    event_type: str
    session_id: UUID
    occurred_at: datetime
    provider: ProviderName | None
    attempt_id: UUID | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SessionEvent:
    event_id: UUID
    schema_version: int
    event_type: str
    session_id: UUID
    occurred_at: datetime
    provider: ProviderName | None
    attempt_id: UUID | None
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        event_type: str,
        session_id: UUID,
        *,
        provider: ProviderName | None = None,
        attempt_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> "SessionEvent":
        from backend.events.registry import validate_payload

        normalized = validate_payload(event_type, payload or {})
        timestamp = occurred_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("Event timestamps must include a timezone")
        return cls(
            event_id=event_id or uuid4(),
            schema_version=1,
            event_type=event_type,
            session_id=session_id,
            occurred_at=timestamp.astimezone(UTC),
            provider=provider,
            attempt_id=attempt_id,
            payload=normalized,
        )

    @classmethod
    def from_json(cls, value: bytes | str) -> "SessionEvent":
        from backend.events.registry import validate_payload

        raw = json.loads(value)
        envelope = _Envelope.model_validate(raw)
        if envelope.schema_version != 1:
            raise ValueError(f"Unsupported event schema version {envelope.schema_version}")
        if envelope.occurred_at.tzinfo is None:
            raise ValueError("Event timestamps must include a timezone")
        payload = validate_payload(envelope.event_type, envelope.payload)
        return cls(
            event_id=envelope.event_id,
            schema_version=envelope.schema_version,
            event_type=envelope.event_type,
            session_id=envelope.session_id,
            occurred_at=envelope.occurred_at.astimezone(UTC),
            provider=envelope.provider,
            attempt_id=envelope.attempt_id,
            payload=payload,
        )

    def to_json(self) -> bytes:
        value = {
            "event_id": str(self.event_id),
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "session_id": str(self.session_id),
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "provider": self.provider.value if self.provider else None,
            "attempt_id": str(self.attempt_id) if self.attempt_id else None,
            "payload": self.payload,
        }
        return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()

    @property
    def subject(self) -> str:
        return f"harbor.v1.events.session.{self.session_id}"
