"""Use Camoufox as the conservative routing default."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_06"
down_revision: str | None = "20260717_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE routing_configuration SET default_provider = 'camoufox' "
        "WHERE key = 'global' AND default_provider = 'chromium' "
        "AND configuration_version = 1"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE routing_configuration SET default_provider = 'chromium' "
        "WHERE key = 'global' AND default_provider = 'camoufox' "
        "AND configuration_version = 1"
    )
