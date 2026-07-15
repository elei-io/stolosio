from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Sequence,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.session import Base

queue_sequence = Sequence("harbor_gateway_queue_sequence")


class ProviderState(Base):
    __tablename__ = "gateway_provider_state"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)


class GatewaySession(Base):
    __tablename__ = "gateway_sessions"
    __table_args__ = (
        Index("ix_gateway_sessions_provider_state", "resolved_provider", "state"),
        Index("ix_gateway_sessions_queue", "resolved_provider", "state", "queue_sequence"),
        Index("ix_gateway_sessions_lease", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    resolved_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    setting_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_sequence: Mapped[int] = mapped_column(
        BigInteger,
        queue_sequence,
        server_default=queue_sequence.next_value(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquiring_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))


class SessionEventRecord(Base):
    __tablename__ = "session_events"
    __table_args__ = (
        Index("ix_session_events_session", "session_id", "id"),
        Index("ix_session_events_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
