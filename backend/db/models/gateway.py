from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


class ProviderFleet(Base):
    __tablename__ = "provider_fleets"
    __table_args__ = (
        CheckConstraint("minimum_instances >= 0", name="ck_fleet_minimum_nonnegative"),
        CheckConstraint(
            "maximum_instances >= minimum_instances", name="ck_fleet_maximum_gte_minimum"
        ),
        CheckConstraint("session_capacity_per_instance >= 1", name="ck_fleet_capacity_positive"),
        CheckConstraint("scale_down_cooldown_seconds > 0", name="ck_fleet_cooldown_positive"),
    )

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    minimum_instances: Mapped[int] = mapped_column(nullable=False)
    maximum_instances: Mapped[int] = mapped_column(nullable=False)
    session_capacity_per_instance: Mapped[int] = mapped_column(nullable=False)
    scale_down_cooldown_seconds: Mapped[int] = mapped_column(nullable=False)
    desired_instances: Mapped[int] = mapped_column(nullable=False)
    configuration_version: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_scale_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scale_down_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    controller_status: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderInstance(Base):
    __tablename__ = "provider_instances"
    __table_args__ = (
        Index("ix_provider_instances_provider_state", "provider", "state"),
        Index("ix_provider_instances_observation_expiry", "observation_expires_at"),
        CheckConstraint("capacity >= 1", name="ck_provider_instance_capacity_positive"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(
        String(32), ForeignKey("provider_fleets.provider", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    capacity: Mapped[int] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draining_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FleetConfigurationEvent(Base):
    __tablename__ = "fleet_configuration_events"
    __table_args__ = (Index("ix_fleet_configuration_events_provider", "provider", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(
        String(32), ForeignKey("provider_fleets.provider", ondelete="CASCADE"), nullable=False
    )
    configuration_version: Mapped[int] = mapped_column(nullable=False)
    previous_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
    qualification_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )


class AcquisitionAttempt(Base):
    __tablename__ = "acquisition_attempts"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal"),
        Index("ix_acquisition_attempts_provider_state", "provider", "state"),
        Index("ix_acquisition_attempts_instance_state", "provider_instance_id", "state"),
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
    provider_instance_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("provider_instances.id", ondelete="SET NULL")
    )
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
    domain_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="SET NULL"), index=True
    )
    routing_reason: Mapped[str | None] = mapped_column(String(32))
    routing_version: Mapped[int | None]
    estimated_cost_units: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_units: Mapped[int | None] = mapped_column(BigInteger)
    cost_projected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


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
