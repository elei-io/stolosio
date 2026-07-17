from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.session import Base


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(253), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    session_count: Mapped[int] = mapped_column(BigInteger, default=0)
    eligible_acquisition_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class SessionDomain(Base):
    __tablename__ = "session_domains"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("gateway_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DomainCommandStat(Base):
    __tablename__ = "domain_command_stats"

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    method: Mapped[str] = mapped_column(String(128), primary_key=True)
    command_count: Mapped[int] = mapped_column(BigInteger, default=0)
    session_count: Mapped[int] = mapped_column(BigInteger, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DomainPromotionStat(Base):
    __tablename__ = "domain_promotion_stats"

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    promotion_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_trigger_method: Mapped[str] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionDomainCommand(Base):
    __tablename__ = "session_domain_commands"
    __table_args__ = (Index("ix_session_domain_commands_domain", "domain_id"),)

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("gateway_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    method: Mapped[str] = mapped_column(String(128), primary_key=True)


class RoutingConfiguration(Base):
    __tablename__ = "routing_configuration"
    __table_args__ = (
        CheckConstraint(
            "existing_domain_probe_rate_basis_points BETWEEN 0 AND 10000",
            name="ck_routing_probe_rate",
        ),
        CheckConstraint(
            "required_successful_probes >= 1",
            name="ck_routing_required_probes",
        ),
    )

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    default_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    existing_domain_probe_rate_basis_points: Mapped[int] = mapped_column(
        nullable=False, default=100, server_default="100"
    )
    required_successful_probes: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    comparison_policy_version: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    configuration_version: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderRoutingProfile(Base):
    __tablename__ = "provider_routing_profiles"
    __table_args__ = (
        CheckConstraint("cost_units_per_second >= 0", name="ck_provider_cost_nonnegative"),
    )

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    automatic_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    cost_units_per_second: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capability_manifest_version: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DomainProviderProfile(Base):
    __tablename__ = "domain_provider_profiles"
    __table_args__ = (
        CheckConstraint(
            "qualification_state IN ('unqualified', 'probing', 'qualified', 'rejected')",
            name="ck_domain_provider_qualification_state",
        ),
    )

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    qualification_state: Mapped[str] = mapped_column(String(16), nullable=False)
    successful_probe_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    failed_probe_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    observed_session_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    total_cost_units: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    last_status_code: Mapped[int | None]
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comparison_policy_version: Mapped[int] = mapped_column(nullable=False)


class QualificationProbe(Base):
    __tablename__ = "qualification_probes"
    __table_args__ = (
        UniqueConstraint("source_session_id", "candidate_provider"),
        Index("ix_qualification_probes_queue", "state", "created_at"),
        Index(
            "ux_qualification_probes_initial",
            "domain_id",
            "candidate_provider",
            unique=True,
            postgresql_where=text("trigger = 'new_domain'"),
        ),
        CheckConstraint(
            "trigger IN ('new_domain', 'existing_sample')",
            name="ck_qualification_probe_trigger",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed')",
            name="ck_qualification_probe_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    source_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("gateway_sessions.id", ondelete="CASCADE"), nullable=False
    )
    candidate_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    sampling_bucket: Mapped[int | None]
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    baseline_status: Mapped[int] = mapped_column(nullable=False)
    baseline_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_console_errors: Mapped[int] = mapped_column(nullable=False, default=0)
    baseline_content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_status: Mapped[int | None]
    candidate_headers: Mapped[dict | None] = mapped_column(JSONB)
    candidate_console_errors: Mapped[int | None]
    candidate_content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    comparison_outcome: Mapped[str | None] = mapped_column(String(32))
    cost_units: Mapped[int | None] = mapped_column(BigInteger)
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
