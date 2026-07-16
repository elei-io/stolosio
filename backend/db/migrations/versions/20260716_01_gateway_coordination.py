"""Create Harbor's initial gateway and observability schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("harbor_attempt_queue_sequence")))
    op.create_table(
        "gateway_state",
        sa.Column("key", sa.String(32), primary_key=True),
    )
    op.create_table(
        "gateway_provider_state",
        sa.Column("provider", sa.String(32), primary_key=True),
    )
    op.create_table(
        "gateway_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("client_reference", sa.String(36), nullable=True),
        sa.Column("requested_settings", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_gateway_sessions_state", "gateway_sessions", ["state"])
    op.create_index("ix_gateway_sessions_lease", "gateway_sessions", ["lease_expires_at"])
    op.create_index(
        "ux_gateway_sessions_client_reference",
        "gateway_sessions",
        ["client_reference"],
        unique=True,
    )
    op.create_table(
        "acquisition_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("resolved_settings", postgresql.JSONB(), nullable=False),
        sa.Column("setting_sources", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "queue_sequence",
            sa.BigInteger(),
            server_default=sa.text("nextval('harbor_attempt_queue_sequence')"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acquiring_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
        sa.UniqueConstraint("session_id", "ordinal"),
    )
    op.create_index(
        "ix_acquisition_attempts_provider_state",
        "acquisition_attempts",
        ["provider", "state"],
    )
    op.create_index(
        "ix_acquisition_attempts_queue",
        "acquisition_attempts",
        ["provider", "state", "queue_sequence"],
    )
    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_session_events_session", "session_events", ["session_id", "id"])
    op.create_index("ix_session_events_occurred", "session_events", ["occurred_at"])
    op.create_index("ix_session_events_attempt_id", "session_events", ["attempt_id"])
    op.create_index("ix_session_events_published_at", "session_events", ["published_at"])

    op.create_table(
        "domains",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("hostname", sa.String(253), unique=True, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_count", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_index("ix_domains_last_seen_at", "domains", ["last_seen_at"])
    op.create_table(
        "session_domains",
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "domain_command_stats",
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("method", sa.String(128), primary_key=True),
        sa.Column("command_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("session_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "session_domain_commands",
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("gateway_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "domain_id",
            sa.BigInteger(),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("method", sa.String(128), primary_key=True),
    )
    op.create_index(
        "ix_session_domain_commands_domain",
        "session_domain_commands",
        ["domain_id"],
    )


def downgrade() -> None:
    op.drop_table("session_domain_commands")
    op.drop_table("domain_command_stats")
    op.drop_table("session_domains")
    op.drop_table("domains")
    op.drop_table("session_events")
    op.drop_table("acquisition_attempts")
    op.drop_table("gateway_sessions")
    op.drop_table("gateway_provider_state")
    op.drop_table("gateway_state")
    op.execute(sa.schema.DropSequence(sa.Sequence("harbor_attempt_queue_sequence")))
