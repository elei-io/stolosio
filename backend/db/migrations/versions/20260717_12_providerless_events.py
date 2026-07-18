"""Store providerless session events as NULL."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_12"
down_revision: str | None = "20260717_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE session_events
        SET provider = NULL
        WHERE provider = 'unknown'
        """
    )


def downgrade() -> None:
    raise NotImplementedError("The providerless-event cutover is irreversible")
