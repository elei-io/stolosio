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


class DomainProviderTransitionStat(Base):
    __tablename__ = "domain_provider_transition_stats"

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    from_provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    to_provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32), primary_key=True)
    transition_count: Mapped[int] = mapped_column(BigInteger, default=0)
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
            "required_support_confirmations >= 1",
            name="ck_routing_required_support_confirmations",
        ),
    )

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    default_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    existing_domain_probe_rate_basis_points: Mapped[int] = mapped_column(
        nullable=False, default=100, server_default="100"
    )
    required_support_confirmations: Mapped[int] = mapped_column(
        nullable=False, default=1, server_default="1"
    )
    support_policy_version: Mapped[int] = mapped_column(
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


class DomainProviderSupport(Base):
    __tablename__ = "domain_provider_support"
    __table_args__ = (
        CheckConstraint(
            "support_state IN ('unknown', 'checking', 'supported', 'unsupported')",
            name="ck_domain_provider_support_state",
        ),
    )

    domain_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    support_state: Mapped[str] = mapped_column(String(16), nullable=False)
    successful_probe_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    failed_probe_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    inconclusive_probe_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    observed_session_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    total_cost_units: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    navigation_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    status_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    headers_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    method_coverage_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    content_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    method_observed_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    method_declared_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    unsupported_methods: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    failure_reason_code: Mapped[str | None] = mapped_column(String(64))
    last_status_code: Mapped[int | None]
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_supported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    support_policy_version: Mapped[int] = mapped_column(nullable=False)
    capability_manifest_version: Mapped[int] = mapped_column(nullable=False)


class SupportProbe(Base):
    __tablename__ = "support_probes"
    __table_args__ = (
        UniqueConstraint("source_session_id", "candidate_provider"),
        Index("ix_support_probes_queue", "state", "created_at"),
        Index(
            "ux_support_probes_initial",
            "domain_id",
            "candidate_provider",
            unique=True,
            postgresql_where=text("trigger = 'new_domain'"),
        ),
        CheckConstraint(
            "trigger IN ('new_domain', 'existing_sample')",
            name="ck_support_probe_trigger",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'failed')",
            name="ck_support_probe_state",
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
    required_methods: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(16))
    navigation_state: Mapped[str | None] = mapped_column(String(16))
    status_state: Mapped[str | None] = mapped_column(String(16))
    headers_state: Mapped[str | None] = mapped_column(String(16))
    method_coverage_state: Mapped[str | None] = mapped_column(String(16))
    content_state: Mapped[str | None] = mapped_column(String(16))
    status_code: Mapped[int | None]
    reason_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    method_observed_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    method_declared_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    unsupported_methods: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    content_facts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cost_units: Mapped[int | None] = mapped_column(BigInteger)
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
