from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Sequence,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.session import Base

attempt_queue_sequence = Sequence("harbor_attempt_queue_sequence")


class GatewayState(Base):
    __tablename__ = "gateway_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)


class ProviderState(Base):
    __tablename__ = "gateway_provider_state"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)


class GatewaySession(Base):
    __tablename__ = "gateway_sessions"
    __table_args__ = (
        Index("ix_gateway_sessions_state", "state"),
        Index("ix_gateway_sessions_lease", "lease_expires_at"),
        Index("ux_gateway_sessions_client_reference", "client_reference", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False)
    client_reference: Mapped[str | None] = mapped_column(String(36))
    requested_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))


class AcquisitionAttempt(Base):
    __tablename__ = "acquisition_attempts"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal"),
        Index("ix_acquisition_attempts_provider_state", "provider", "state"),
        Index(
            "ix_acquisition_attempts_queue",
            "provider",
            "state",
            "queue_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("gateway_sessions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    setting_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_sequence: Mapped[int] = mapped_column(
        BigInteger,
        attempt_queue_sequence,
        server_default=attempt_queue_sequence.next_value(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquiring_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))


class SessionEventRecord(Base):
    __tablename__ = "session_events"
    __table_args__ = (
        Index("ix_session_events_session", "session_id", "id"),
        Index("ix_session_events_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, nullable=False, default=uuid4
    )
    schema_version: Mapped[int] = mapped_column(default=1, nullable=False)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(36), index=True)
    provider: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
