from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import Domain, GatewaySession, SessionEventRecord


class RetentionJob:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        event_days: int,
        terminal_session_days: int,
        domain_days: int,
        batch_size: int,
    ) -> None:
        self._sessions = sessions
        self._event_days = event_days
        self._terminal_session_days = terminal_session_days
        self._domain_days = domain_days
        self._batch_size = batch_size

    async def run_once(self) -> dict[str, int]:
        now = datetime.now(UTC)
        deleted: dict[str, int] = {}
        async with self._sessions.begin() as database:
            event_ids = (
                select(SessionEventRecord.id)
                .where(
                    SessionEventRecord.occurred_at < now - timedelta(days=self._event_days),
                    SessionEventRecord.published_at.is_not(None),
                )
                .limit(self._batch_size)
            )
            result = await database.execute(
                delete(SessionEventRecord).where(SessionEventRecord.id.in_(event_ids))
            )
            deleted["session_events"] = result.rowcount

        async with self._sessions.begin() as database:
            session_ids = (
                select(GatewaySession.id)
                .where(
                    GatewaySession.state.in_(("closed", "failed")),
                    GatewaySession.closed_at < now - timedelta(days=self._terminal_session_days),
                )
                .limit(self._batch_size)
            )
            result = await database.execute(
                delete(GatewaySession).where(GatewaySession.id.in_(session_ids))
            )
            deleted["gateway_sessions"] = result.rowcount

        async with self._sessions.begin() as database:
            domain_ids = (
                select(Domain.id)
                .where(Domain.last_seen_at < now - timedelta(days=self._domain_days))
                .limit(self._batch_size)
            )
            result = await database.execute(delete(Domain).where(Domain.id.in_(domain_ids)))
            deleted["domains"] = result.rowcount
        return deleted
