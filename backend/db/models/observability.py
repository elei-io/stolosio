from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.session import Base


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(253), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    session_count: Mapped[int] = mapped_column(BigInteger, default=0)


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
