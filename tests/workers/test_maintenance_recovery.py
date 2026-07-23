from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import GatewaySession, SessionEventRecord
from backend.events import EventType, SessionEvent
from backend.workers.maintenance.__main__ import _rehydrate_event_stream


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[SessionEvent] = []

    async def publish(self, event: SessionEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_fresh_event_stream_is_rehydrated_from_postgres(
    database_sessions: async_sessionmaker[AsyncSession],
) -> None:
    session_id = uuid4()
    event_id = uuid4()
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        database.add(
            GatewaySession(
                id=str(session_id),
                owner_id="test-owner",
                client_reference=str(uuid4()),
                lease_token=str(uuid4()),
                requested_settings={},
                state="closed",
                lease_expires_at=now,
                closed_at=now,
            )
        )
        database.add(
            SessionEventRecord(
                event_id=event_id,
                schema_version=1,
                session_id=str(session_id),
                event_type=EventType.SESSION_CLOSED.value,
                occurred_at=now,
                payload={"reason": "client_disconnected"},
                published_at=now,
            )
        )
    publisher = RecordingPublisher()

    published = await _rehydrate_event_stream(
        publisher,
        sessions=database_sessions,
    )

    assert published == 1
    assert [event.event_id for event in publisher.events] == [event_id]
