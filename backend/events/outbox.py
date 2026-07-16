from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SessionEventRecord
from backend.events.contracts import SessionEvent
from backend.events.publisher import EventPublisher
from backend.proxy.contracts import ProviderName


class LifecycleOutboxPublisher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
    ) -> None:
        self._sessions = sessions
        self._publisher = publisher

    async def publish_pending(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> int:
        published = 0
        async with self._sessions.begin() as database:
            statement = (
                select(SessionEventRecord)
                .where(SessionEventRecord.published_at.is_(None))
                .order_by(SessionEventRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            if session_id is not None:
                statement = statement.where(SessionEventRecord.session_id == session_id)
            rows = list(await database.scalars(statement))
            for row in rows:
                event = SessionEvent(
                    event_id=row.event_id,
                    schema_version=row.schema_version,
                    event_type=row.event_type,
                    session_id=UUID(row.session_id),
                    occurred_at=row.occurred_at,
                    provider=ProviderName(row.provider) if row.provider else None,
                    attempt_id=UUID(row.attempt_id) if row.attempt_id else None,
                    payload=row.payload,
                )
                await self._publisher.publish(event)
                row.published_at = datetime.now(UTC)
                published += 1
        return published


class NullLifecycleOutboxPublisher:
    async def publish_pending(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> int:
        return 0
