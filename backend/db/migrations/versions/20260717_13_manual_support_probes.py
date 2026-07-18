"""Allow repeatable manual support probes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_13"
down_revision: str | None = "20260717_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "support_probes_source_session_id_candidate_provider_key",
        "support_probes",
        type_="unique",
    )
    op.create_index(
        "ux_support_probes_automatic_source",
        "support_probes",
        ["source_session_id", "candidate_provider"],
        unique=True,
        postgresql_where=sa.text(
            "trigger IN ('new_domain', 'existing_sample')"
        ),
    )
    op.drop_constraint(
        "ck_support_probe_trigger",
        "support_probes",
        type_="check",
    )
    op.create_check_constraint(
        "ck_support_probe_trigger",
        "support_probes",
        "trigger IN ('new_domain', 'existing_sample', 'manual')",
    )


def downgrade() -> None:
    raise NotImplementedError("Manual support probes are an irreversible cutover")
