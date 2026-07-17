from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import Domain, DomainPromotionStat, SessionEventRecord
from backend.events import EventType, SessionEvent
from backend.proxy.contracts import ProviderName


class PromotionHistoryRepository:
    """Durable factual evidence that a domain has required browser promotion."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def requires_browser(self, hostname: str) -> bool:
        async with self._sessions() as database:
            count = await database.scalar(
                select(DomainPromotionStat.promotion_count)
                .join(Domain, Domain.id == DomainPromotionStat.domain_id)
                .where(Domain.hostname == hostname)
            )
        return bool(count)

    async def record(self, hostname: str, trigger_method: str) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as database:
            domain_id = await database.scalar(
                insert(Domain)
                .values(
                    hostname=hostname,
                    first_seen_at=now,
                    last_seen_at=now,
                    session_count=0,
                )
                .on_conflict_do_update(
                    index_elements=[Domain.hostname],
                    set_={"last_seen_at": now},
                )
                .returning(Domain.id)
            )
            assert domain_id is not None
            await database.execute(
                insert(DomainPromotionStat)
                .values(
                    domain_id=domain_id,
                    promotion_count=1,
                    last_trigger_method=trigger_method,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[DomainPromotionStat.domain_id],
                    set_={
                        "promotion_count": DomainPromotionStat.promotion_count + 1,
                        "last_trigger_method": trigger_method,
                        "last_seen_at": now,
                    },
                )
            )

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
            EventType.EXECUTION_PROMOTED,
            UUID(session_id),
            provider=to_provider,
            attempt_id=UUID(attempt_id),
            payload={
                "from_provider": from_provider,
                "to_provider": to_provider.value,
                "trigger_method": trigger_method,
            },
        )
        async with self._sessions.begin() as database:
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
