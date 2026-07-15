"""Create transactional gateway coordination tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("harbor_gateway_queue_sequence")))
    op.create_table(
        "gateway_provider_state",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "gateway_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=False),
        sa.Column("requested_provider", sa.String(length=32), nullable=False),
        sa.Column("resolved_provider", sa.String(length=32), nullable=False),
        sa.Column("requested_settings", postgresql.JSONB(), nullable=False),
        sa.Column("resolved_settings", postgresql.JSONB(), nullable=False),
        sa.Column("setting_sources", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "queue_sequence",
            sa.BigInteger(),
            server_default=sa.text("nextval('harbor_gateway_queue_sequence')"),
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
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gateway_sessions_provider_state",
        "gateway_sessions",
        ["resolved_provider", "state"],
    )
    op.create_index(
        "ix_gateway_sessions_queue",
        "gateway_sessions",
        ["resolved_provider", "state", "queue_sequence"],
    )
    op.create_index(
        "ix_gateway_sessions_lease",
        "gateway_sessions",
        ["lease_expires_at"],
    )
    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["gateway_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_events_session",
        "session_events",
        ["session_id", "id"],
    )
    op.create_index(
        "ix_session_events_occurred",
        "session_events",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("session_events")
    op.drop_table("gateway_sessions")
    op.drop_table("gateway_provider_state")
    op.execute(sa.schema.DropSequence(sa.Sequence("harbor_gateway_queue_sequence")))
