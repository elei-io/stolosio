from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainCommandStat,
    DomainProviderSupport,
    GatewaySession,
    SessionDomain,
    SessionDomainCommand,
    SessionEventRecord,
)
from backend.events import EventType, SessionEvent
from backend.events.normalization import normalize_domain


class EventRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, events: list[SessionEvent]) -> int:
        recorded = 0
        async with self._sessions.begin() as database:
            for event in events:
                inserted = await database.scalar(
                    insert(SessionEventRecord)
                    .values(
                        event_id=event.event_id,
                        schema_version=event.schema_version,
                        session_id=str(event.session_id),
                        attempt_id=str(event.attempt_id) if event.attempt_id else None,
                        event_type=event.event_type,
                        provider=event.provider.value if event.provider else "unknown",
                        reason=event.payload.get("reason"),
                        occurred_at=event.occurred_at,
                        payload=event.payload,
                        published_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(index_elements=[SessionEventRecord.event_id])
                    .returning(SessionEventRecord.id)
                )
                if inserted is None:
                    continue
                recorded += 1
                await self._project(database, event)
        return recorded

    async def known_session_ids(self, session_ids: set[str]) -> set[str]:
        if not session_ids:
            return set()
        async with self._sessions() as database:
            rows = await database.scalars(
                select(GatewaySession.id).where(GatewaySession.id.in_(session_ids))
            )
            return set(rows)

    async def _project(self, database: AsyncSession, event: SessionEvent) -> None:
        event_type = EventType(event.event_type)
        url = event.payload.get("url")
        if isinstance(url, str):
            domain = normalize_domain(url)
            if domain is not None:
                domain_id = await self._observe_domain(
                    database,
                    str(event.session_id),
                    domain,
                    event.occurred_at,
                )
                if (
                    event_type is EventType.NAVIGATION_RESPONSE
                    and event.attempt_id is not None
                    and event.provider is not None
                ):
                    attempt = await database.get(
                        AcquisitionAttempt,
                        str(event.attempt_id),
                        with_for_update=True,
                    )
                    if attempt is not None:
                        attempt.domain_id = domain_id
                    await self._project_cost(database, attempt)

        if event_type is EventType.COMMAND_RECEIVED:
            domain = event.payload.get("domain")
            method = event.payload.get("method")
            if isinstance(domain, str) and isinstance(method, str):
                await self._observe_command(
                    database,
                    str(event.session_id),
                    domain,
                    method,
                    event.occurred_at,
                )
        if event_type in {EventType.ATTEMPT_CLOSED, EventType.ATTEMPT_FAILED}:
            attempt = (
                await database.get(
                    AcquisitionAttempt,
                    str(event.attempt_id),
                    with_for_update=True,
                )
                if event.attempt_id is not None
                else None
            )
            await self._project_cost(database, attempt)

    @staticmethod
    async def _project_cost(
        database: AsyncSession,
        attempt: AcquisitionAttempt | None,
    ) -> None:
        if (
            attempt is None
            or attempt.domain_id is None
            or attempt.actual_cost_units is None
            or attempt.cost_projected
        ):
            return
        await database.execute(
            update(DomainProviderSupport)
            .where(
                DomainProviderSupport.domain_id == attempt.domain_id,
                DomainProviderSupport.provider == attempt.provider,
            )
            .values(
                observed_session_count=DomainProviderSupport.observed_session_count + 1,
                total_cost_units=(
                    DomainProviderSupport.total_cost_units + attempt.actual_cost_units
                ),
            )
        )
        attempt.cost_projected = True

    async def _observe_domain(
        self,
        database: AsyncSession,
        session_id: str,
        hostname: str,
        occurred_at: datetime,
    ) -> int:
        domain_id = await database.scalar(
            insert(Domain)
            .values(
                hostname=hostname,
                first_seen_at=occurred_at,
                last_seen_at=occurred_at,
                session_count=0,
            )
            .on_conflict_do_update(
                index_elements=[Domain.hostname],
                set_={
                    "first_seen_at": func.least(Domain.first_seen_at, occurred_at),
                    "last_seen_at": func.greatest(Domain.last_seen_at, occurred_at),
                },
            )
            .returning(Domain.id)
        )
        assert domain_id is not None
        new_session_domain = await database.scalar(
            insert(SessionDomain)
            .values(
                session_id=session_id,
                domain_id=domain_id,
                first_seen_at=occurred_at,
                last_seen_at=occurred_at,
            )
            .on_conflict_do_update(
                index_elements=[SessionDomain.session_id, SessionDomain.domain_id],
                set_={
                    "first_seen_at": func.least(SessionDomain.first_seen_at, occurred_at),
                    "last_seen_at": func.greatest(SessionDomain.last_seen_at, occurred_at),
                },
            )
            .returning(SessionDomain.first_seen_at)
        )
        if new_session_domain == occurred_at:
            row = await database.get(Domain, domain_id, with_for_update=True)
            assert row is not None
            # The equality above is also true for a repeat at the exact same timestamp.
            # Raw event idempotency prevents ordinary redelivery; the join count is
            # corrected by querying whether this was its first persisted observation.
            count = await database.scalar(
                select(func.count())
                .select_from(SessionDomain)
                .where(SessionDomain.domain_id == domain_id)
            )
            row.session_count = int(count or 0)
        return domain_id

    async def _observe_command(
        self,
        database: AsyncSession,
        session_id: str,
        hostname: str,
        method: str,
        occurred_at: datetime,
    ) -> None:
        domain_id = await self._observe_domain(database, session_id, hostname, occurred_at)
        new_session_command = await database.scalar(
            insert(SessionDomainCommand)
            .values(session_id=session_id, domain_id=domain_id, method=method)
            .on_conflict_do_nothing(
                index_elements=[
                    SessionDomainCommand.session_id,
                    SessionDomainCommand.domain_id,
                    SessionDomainCommand.method,
                ]
            )
            .returning(SessionDomainCommand.session_id)
        )
        await database.execute(
            insert(DomainCommandStat)
            .values(
                domain_id=domain_id,
                method=method,
                command_count=1,
                session_count=1 if new_session_command is not None else 0,
                first_seen_at=occurred_at,
                last_seen_at=occurred_at,
            )
            .on_conflict_do_update(
                index_elements=[DomainCommandStat.domain_id, DomainCommandStat.method],
                set_={
                    "command_count": DomainCommandStat.command_count + 1,
                    "session_count": DomainCommandStat.session_count
                    + (1 if new_session_command is not None else 0),
                    "first_seen_at": func.least(DomainCommandStat.first_seen_at, occurred_at),
                    "last_seen_at": func.greatest(DomainCommandStat.last_seen_at, occurred_at),
                },
            )
        )
