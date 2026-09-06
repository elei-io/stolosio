from collections.abc import AsyncIterator
from uuid import UUID

from nats.aio.client import Client as NatsClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import SessionEventRecord
from backend.events import SessionEvent
from backend.events.contracts import provider_from_storage


class HistoricalDebugTimeline:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def events(self, session_id: UUID, *, limit: int = 1_000) -> list[SessionEvent]:
        async with self._sessions() as database:
            rows = list(
                await database.scalars(
                    select(SessionEventRecord)
                    .where(SessionEventRecord.session_id == str(session_id))
                    .order_by(SessionEventRecord.occurred_at, SessionEventRecord.id)
                    .limit(limit)
                )
            )
        return [
            SessionEvent(
                event_id=row.event_id,
                schema_version=row.schema_version,
                event_type=row.event_type,
                session_id=UUID(row.session_id),
                occurred_at=row.occurred_at,
                provider=provider_from_storage(row.provider),
                attempt_id=UUID(row.attempt_id) if row.attempt_id else None,
                payload=row.payload,
            )
            for row in rows
        ]


class LiveDebugTimeline:
    def __init__(self, client: NatsClient) -> None:
        self._client = client

    async def events(self, session_id: UUID) -> AsyncIterator[SessionEvent]:
        subscription = await self._client.subscribe(f"stolosio.v1.events.session.{session_id}")
        try:
            async for message in subscription.messages:
                yield SessionEvent.from_json(message.data)
        finally:
            await subscription.unsubscribe()
