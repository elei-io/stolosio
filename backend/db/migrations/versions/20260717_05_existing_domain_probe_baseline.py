"""Treat domains known before qualification as existing domains."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_05"
down_revision: str | None = "20260717_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE domains SET eligible_acquisition_count = session_count "
        "WHERE eligible_acquisition_count = 0 AND session_count > 0"
    )


def downgrade() -> None:
    pass
