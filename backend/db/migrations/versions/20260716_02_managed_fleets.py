"""Add Harbor-managed provider fleets and instance slot assignment."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_02"
down_revision: str | None = "20260716_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_fleets",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("minimum_instances", sa.Integer(), nullable=False),
        sa.Column("maximum_instances", sa.Integer(), nullable=False),
        sa.Column("session_capacity_per_instance", sa.Integer(), nullable=False),
        sa.Column("scale_down_cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("desired_instances", sa.Integer(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_scale_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scale_down_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("controller_status", sa.String(64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("minimum_instances >= 0", name="ck_fleet_minimum_nonnegative"),
        sa.CheckConstraint(
            "maximum_instances >= minimum_instances", name="ck_fleet_maximum_gte_minimum"
        ),
        sa.CheckConstraint("session_capacity_per_instance >= 1", name="ck_fleet_capacity_positive"),
        sa.CheckConstraint("scale_down_cooldown_seconds > 0", name="ck_fleet_cooldown_positive"),
    )
    op.create_table(
        "provider_instances",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "provider",
            sa.String(32),
            sa.ForeignKey("provider_fleets.provider", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(512), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draining_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("capacity >= 1", name="ck_provider_instance_capacity_positive"),
    )
    op.create_index(
        "ix_provider_instances_provider_state",
        "provider_instances",
        ["provider", "state"],
    )
    op.create_index(
        "ix_provider_instances_observation_expiry",
        "provider_instances",
        ["observation_expires_at"],
    )
    op.create_table(
        "fleet_configuration_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "provider",
            sa.String(32),
            sa.ForeignKey("provider_fleets.provider", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("previous_values", postgresql.JSONB(), nullable=False),
        sa.Column("new_values", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_fleet_configuration_events_provider",
        "fleet_configuration_events",
        ["provider", "id"],
    )
    op.add_column(
        "acquisition_attempts",
        sa.Column("provider_instance_id", sa.String(128), nullable=True),
    )
    op.create_foreign_key(
        "fk_acquisition_attempts_provider_instance",
        "acquisition_attempts",
        "provider_instances",
        ["provider_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_acquisition_attempts_instance_state",
        "acquisition_attempts",
        ["provider_instance_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_acquisition_attempts_instance_state", table_name="acquisition_attempts")
    op.drop_constraint(
        "fk_acquisition_attempts_provider_instance",
        "acquisition_attempts",
        type_="foreignkey",
    )
    op.drop_column("acquisition_attempts", "provider_instance_id")
    op.drop_table("fleet_configuration_events")
    op.drop_table("provider_instances")
    op.drop_table("provider_fleets")
