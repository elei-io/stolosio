from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    DomainProviderTransitionStat,
    SessionEventRecord,
)
from backend.events import EventType, SessionEvent
from backend.proxy.contracts import ProviderName


class ProviderTransitionRepository:
    """Stores factual runtime transitions without turning them into routing policy."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record_transition(
        self,
        session_id: str,
        attempt_id: str,
        *,
        from_provider: str,
        to_provider: ProviderName,
        trigger_method: str,
    ) -> None:
        event = SessionEvent.create(
            EventType.EXECUTION_TRANSITIONED,
            UUID(session_id),
            provider=to_provider,
            attempt_id=UUID(attempt_id),
            payload={
                "from_provider": from_provider,
                "to_provider": to_provider.value,
                "trigger_method": trigger_method,
            },
        )
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            attempt = await database.get(AcquisitionAttempt, attempt_id)
            if attempt is not None and attempt.domain_id is not None:
                await database.execute(
                    insert(DomainProviderTransitionStat)
                    .values(
                        domain_id=attempt.domain_id,
                        from_provider=from_provider,
                        to_provider=to_provider.value,
                        trigger="new_requirement",
                        transition_count=1,
                        last_trigger_method=trigger_method,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            DomainProviderTransitionStat.domain_id,
                            DomainProviderTransitionStat.from_provider,
                            DomainProviderTransitionStat.to_provider,
                            DomainProviderTransitionStat.trigger,
                        ],
                        set_={
                            "transition_count": (
                                DomainProviderTransitionStat.transition_count + 1
                            ),
                            "last_trigger_method": trigger_method,
                            "last_seen_at": now,
                        },
                    )
                )
            database.add(
                SessionEventRecord(
                    event_id=event.event_id,
                    schema_version=event.schema_version,
                    session_id=str(event.session_id),
                    event_type=event.event_type,
                    attempt_id=str(event.attempt_id),
                    provider=event.provider.value,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                )
            )
