from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    DomainProviderCostStat,
    GatewaySession,
    HealthProbe,
    ProviderCommandCostStat,
    SessionDomain,
    SessionEventRecord,
)
from backend.events import EventType, SessionEvent
from backend.events.normalization import normalize_domain
from backend.events.registry import OTHER_COMMAND_METHOD
from backend.proxy.contracts import ProviderName

_MAX_RETAINED_METHODS_PER_PROVIDER = 512
_SESSION_OVERHEAD_METHOD = "__session_overhead__"
_ATTEMPT_PROJECTION_BATCH_SIZE = 25
_ATTEMPT_PROJECTION_EVENTS = {
    EventType.COMMAND_SUMMARY,
    EventType.ATTEMPT_CLOSED,
    EventType.ATTEMPT_FAILED,
}


class EventRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, events: list[SessionEvent]) -> int:
        recorded = 0
        projection_events: list[SessionEvent] = []
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
                        provider=event.provider.value if event.provider else None,
                        reason=event.payload.get("reason"),
                        occurred_at=event.occurred_at,
                        payload=event.payload,
                        published_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(index_elements=[SessionEventRecord.event_id])
                    .returning(SessionEventRecord.id)
                )
                if inserted is None:
                    if self._needs_projection(event):
                        projection_events.append(event)
                    continue
                recorded += 1
                if self._needs_projection(event):
                    projection_events.append(event)
        # Raw event durability is independent of analytical projections. Domain
        # rows and attempts are deliberately projected in separate transactions:
        # request cleanup owns attempt rows and must never wait on a domain row
        # held by a recorder transaction that is itself waiting on an attempt.
        await self._project_events(projection_events)
        return recorded

    @staticmethod
    def _needs_projection(event: SessionEvent) -> bool:
        return isinstance(event.payload.get("url"), str) or EventType(
            event.event_type
        ) in _ATTEMPT_PROJECTION_EVENTS

    async def known_session_ids(self, session_ids: set[str]) -> set[str]:
        if not session_ids:
            return set()
        async with self._sessions() as database:
            rows = await database.scalars(
                select(GatewaySession.id).where(GatewaySession.id.in_(session_ids))
            )
            return set(rows)

    async def _project_events(self, events: list[SessionEvent]) -> None:
        if not events:
            return
        session_ids = {str(event.session_id) for event in events}
        async with self._sessions() as database:
            probe_session_ids = set(
                await database.scalars(
                    select(GatewaySession.id)
                    .join(
                        HealthProbe,
                        HealthProbe.id == GatewaySession.client_reference,
                    )
                    .where(GatewaySession.id.in_(session_ids))
                )
            )
        ordinary_events = [
            event
            for event in events
            if str(event.session_id) not in probe_session_ids
        ]
        probe_attempt_ids = {
            str(event.attempt_id)
            for event in events
            if (
                str(event.session_id) in probe_session_ids
                and event.attempt_id is not None
                and EventType(event.event_type) in _ATTEMPT_PROJECTION_EVENTS
            )
        }
        attempt_domains = await self._project_domain_facts(ordinary_events)
        summary_attempt_ids = {
            str(event.attempt_id)
            for event in ordinary_events
            if (
                event.attempt_id is not None
                and EventType(event.event_type) in _ATTEMPT_PROJECTION_EVENTS
            )
        }
        await self._project_attempts(attempt_domains, summary_attempt_ids)
        await self._complete_probe_attempts(probe_attempt_ids)

    async def _project_domain_facts(
        self,
        events: list[SessionEvent],
    ) -> dict[str, int]:
        domain_ranges: dict[str, tuple[datetime, datetime]] = {}
        session_ranges: dict[tuple[str, str], tuple[datetime, datetime]] = {}
        attempt_hostnames: dict[str, str] = {}
        for event in events:
            url = event.payload.get("url")
            if not isinstance(url, str):
                continue
            hostname = normalize_domain(url)
            if hostname is None:
                continue
            first_seen, last_seen = domain_ranges.get(
                hostname,
                (event.occurred_at, event.occurred_at),
            )
            domain_ranges[hostname] = (
                min(first_seen, event.occurred_at),
                max(last_seen, event.occurred_at),
            )
            session_key = (str(event.session_id), hostname)
            session_first, session_last = session_ranges.get(
                session_key,
                (event.occurred_at, event.occurred_at),
            )
            session_ranges[session_key] = (
                min(session_first, event.occurred_at),
                max(session_last, event.occurred_at),
            )
            if (
                EventType(event.event_type) is EventType.NAVIGATION_RESPONSE
                and event.attempt_id is not None
            ):
                attempt_hostnames[str(event.attempt_id)] = hostname
        if not domain_ranges:
            return {}

        async with self._sessions.begin() as database:
            domain_insert = insert(Domain).values(
                [
                    {
                        "hostname": hostname,
                        "first_seen_at": first_seen,
                        "last_seen_at": last_seen,
                        "session_count": 0,
                    }
                    for hostname, (first_seen, last_seen) in sorted(
                        domain_ranges.items()
                    )
                ]
            )
            excluded_domain = domain_insert.excluded
            domain_rows = await database.execute(
                domain_insert.on_conflict_do_update(
                    index_elements=[Domain.hostname],
                    set_={
                        "first_seen_at": func.least(
                            Domain.first_seen_at,
                            excluded_domain.first_seen_at,
                        ),
                        "last_seen_at": func.greatest(
                            Domain.last_seen_at,
                            excluded_domain.last_seen_at,
                        ),
                    },
                ).returning(Domain.hostname, Domain.id)
            )
            domain_ids = {hostname: domain_id for hostname, domain_id in domain_rows}

            session_values = [
                {
                    "session_id": session_id,
                    "domain_id": domain_ids[hostname],
                    "first_seen_at": first_seen,
                    "last_seen_at": last_seen,
                }
                for (session_id, hostname), (first_seen, last_seen) in sorted(
                    session_ranges.items()
                )
            ]
            if session_values:
                session_insert = insert(SessionDomain).values(session_values)
                excluded_session = session_insert.excluded
                await database.execute(
                    session_insert.on_conflict_do_update(
                        index_elements=[
                            SessionDomain.session_id,
                            SessionDomain.domain_id,
                        ],
                        set_={
                            "first_seen_at": func.least(
                                SessionDomain.first_seen_at,
                                excluded_session.first_seen_at,
                            ),
                            "last_seen_at": func.greatest(
                                SessionDomain.last_seen_at,
                                excluded_session.last_seen_at,
                            ),
                        },
                    )
                )
                observed_domain_ids = sorted(set(domain_ids.values()))
                counts = (
                    select(
                        SessionDomain.domain_id.label("domain_id"),
                        func.count().label("session_count"),
                    )
                    .where(SessionDomain.domain_id.in_(observed_domain_ids))
                    .group_by(SessionDomain.domain_id)
                    .subquery()
                )
                await database.execute(
                    update(Domain)
                    .where(Domain.id == counts.c.domain_id)
                    .values(session_count=counts.c.session_count)
                )
        return {
            attempt_id: domain_ids[hostname]
            for attempt_id, hostname in attempt_hostnames.items()
        }

    async def _project_attempts(
        self,
        attempt_domains: dict[str, int],
        summary_attempt_ids: set[str],
    ) -> None:
        attempt_ids = sorted(set(attempt_domains) | summary_attempt_ids)
        for offset in range(0, len(attempt_ids), _ATTEMPT_PROJECTION_BATCH_SIZE):
            batch_ids = attempt_ids[
                offset : offset + _ATTEMPT_PROJECTION_BATCH_SIZE
            ]
            async with self._sessions.begin() as database:
                attempts = list(
                    await database.scalars(
                        select(AcquisitionAttempt)
                        .where(AcquisitionAttempt.id.in_(batch_ids))
                        .order_by(AcquisitionAttempt.id)
                        .with_for_update()
                    )
                )
                for attempt in attempts:
                    domain_id = attempt_domains.get(attempt.id)
                    if domain_id is not None:
                        attempt.domain_id = domain_id
                    await self._project_cost(database, attempt)
                    if attempt.id in summary_attempt_ids:
                        await self._project_command_summary(database, attempt)

    async def _complete_probe_attempts(self, attempt_ids: set[str]) -> None:
        ordered_ids = sorted(attempt_ids)
        for offset in range(0, len(ordered_ids), _ATTEMPT_PROJECTION_BATCH_SIZE):
            batch_ids = ordered_ids[
                offset : offset + _ATTEMPT_PROJECTION_BATCH_SIZE
            ]
            async with self._sessions.begin() as database:
                attempts = list(
                    await database.scalars(
                        select(AcquisitionAttempt)
                        .where(AcquisitionAttempt.id.in_(batch_ids))
                        .order_by(AcquisitionAttempt.id)
                        .with_for_update()
                    )
                )
                for attempt in attempts:
                    if attempt.finished_at is None:
                        continue
                    attempt.command_cost_projected = True
                    attempt.command_summary = None

    @classmethod
    async def _project_command_summary(
        cls,
        database: AsyncSession,
        attempt: AcquisitionAttempt,
    ) -> None:
        if attempt.finished_at is None or attempt.command_cost_projected:
            return
        await cls._project_cost(database, attempt)
        summary = attempt.command_summary or {}
        methods = summary.get("methods")
        if not isinstance(methods, dict):
            methods = {}
        provider = ProviderName(attempt.provider)
        retained_methods = await cls._retained_command_methods(
            database,
            attempt.provider,
            [method for method in methods if isinstance(method, str)],
        )
        total_provider_ms = sum(
            int(usage.get("provider_latency_ms", 0))
            for usage in methods.values()
            if isinstance(usage, dict)
        )
        browser_ms = (
            0 if provider is ProviderName.HTTP else int(attempt.browser_connected_ms or 0)
        )
        chargeable_ms = int(attempt.chargeable_time_ms or 0)
        attributable_ms = min(browser_ms, total_provider_ms)
        modeled_cost = int(attempt.modeled_cost_units or 0)
        allocated_browser_ms = 0
        allocated_cost = 0
        rows: dict[str, dict[str, object]] = {}
        for method, usage in methods.items():
            if not isinstance(method, str) or not isinstance(usage, dict):
                continue
            provider_ms = int(usage.get("provider_latency_ms", 0))
            method_browser_ms = (
                attributable_ms * provider_ms // total_provider_ms if total_provider_ms else 0
            )
            method_cost = (
                modeled_cost * method_browser_ms // chargeable_ms
                if chargeable_ms
                else 0
            )
            allocated_browser_ms += method_browser_ms
            allocated_cost += method_cost
            cls._accumulate_command_cost(
                rows,
                provider=attempt.provider,
                method=retained_methods[method],
                command_count=int(usage.get("count", 0)),
                failed_count=int(usage.get("failed_count", 0)),
                interrupted_count=int(usage.get("interrupted_count", 0)),
                total_duration_ms=int(usage.get("duration_ms", 0)),
                total_provider_latency_ms=provider_ms,
                total_stolosio_queue_ms=int(usage.get("stolosio_queue_ms", 0)),
                attributed_browser_time_ms=method_browser_ms,
                attributed_cost_units=method_cost,
                observed_at=attempt.finished_at,
            )
        overhead_browser_ms = max(0, browser_ms - allocated_browser_ms)
        overhead_cost = max(0, modeled_cost - allocated_cost)
        if overhead_browser_ms or overhead_cost:
            cls._accumulate_command_cost(
                rows,
                provider=attempt.provider,
                method=_SESSION_OVERHEAD_METHOD,
                command_count=0,
                failed_count=0,
                interrupted_count=0,
                total_duration_ms=0,
                total_provider_latency_ms=0,
                total_stolosio_queue_ms=0,
                attributed_browser_time_ms=overhead_browser_ms,
                attributed_cost_units=overhead_cost,
                observed_at=attempt.finished_at,
            )
        await cls._upsert_command_costs(database, list(rows.values()))
        attempt.command_cost_projected = True
        # This is durable projection input, not long-lived history. The compact
        # DEBUG event remains the historical copy after aggregation succeeds.
        attempt.command_summary = None

    @staticmethod
    async def _retained_command_methods(
        database: AsyncSession,
        provider: str,
        methods: list[str],
    ) -> dict[str, str]:
        existing = set(
            await database.scalars(
                select(ProviderCommandCostStat.method).where(
                    ProviderCommandCostStat.provider == provider,
                    ProviderCommandCostStat.method != _SESSION_OVERHEAD_METHOD,
                )
            )
        )
        exact_methods = existing - {OTHER_COMMAND_METHOD}
        available = max(
            0,
            _MAX_RETAINED_METHODS_PER_PROVIDER - 1 - len(exact_methods),
        )
        retained: dict[str, str] = {}
        for method in methods:
            if method == _SESSION_OVERHEAD_METHOD:
                retained[method] = OTHER_COMMAND_METHOD
            elif method in existing:
                retained[method] = method
            elif available:
                retained[method] = method
                existing.add(method)
                exact_methods.add(method)
                available -= 1
            else:
                retained[method] = OTHER_COMMAND_METHOD
                existing.add(OTHER_COMMAND_METHOD)
        return retained

    @staticmethod
    def _accumulate_command_cost(
        rows: dict[str, dict[str, object]],
        *,
        provider: str,
        method: str,
        command_count: int,
        failed_count: int,
        interrupted_count: int,
        total_duration_ms: int,
        total_provider_latency_ms: int,
        total_stolosio_queue_ms: int,
        attributed_browser_time_ms: int,
        attributed_cost_units: int,
        observed_at: datetime,
    ) -> None:
        row = rows.setdefault(
            method,
            {
                "provider": provider,
                "method": method,
                "command_count": 0,
                "failed_count": 0,
                "interrupted_count": 0,
                "total_duration_ms": 0,
                "total_provider_latency_ms": 0,
                "total_stolosio_queue_ms": 0,
                "attributed_browser_time_ms": 0,
                "attributed_cost_units": 0,
                "first_seen_at": observed_at,
                "last_seen_at": observed_at,
            },
        )
        for key, value in {
            "command_count": command_count,
            "failed_count": failed_count,
            "interrupted_count": interrupted_count,
            "total_duration_ms": total_duration_ms,
            "total_provider_latency_ms": total_provider_latency_ms,
            "total_stolosio_queue_ms": total_stolosio_queue_ms,
            "attributed_browser_time_ms": attributed_browser_time_ms,
            "attributed_cost_units": attributed_cost_units,
        }.items():
            row[key] = int(row[key]) + value

    @staticmethod
    async def _upsert_command_costs(
        database: AsyncSession,
        rows: list[dict[str, object]],
    ) -> None:
        if not rows:
            return
        statement = insert(ProviderCommandCostStat).values(rows)
        excluded = statement.excluded
        await database.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    ProviderCommandCostStat.provider,
                    ProviderCommandCostStat.method,
                ],
                set_={
                    "command_count": (
                        ProviderCommandCostStat.command_count + excluded.command_count
                    ),
                    "failed_count": (ProviderCommandCostStat.failed_count + excluded.failed_count),
                    "interrupted_count": (
                        ProviderCommandCostStat.interrupted_count + excluded.interrupted_count
                    ),
                    "total_duration_ms": (
                        ProviderCommandCostStat.total_duration_ms + excluded.total_duration_ms
                    ),
                    "total_provider_latency_ms": (
                        ProviderCommandCostStat.total_provider_latency_ms
                        + excluded.total_provider_latency_ms
                    ),
                    "total_stolosio_queue_ms": (
                        ProviderCommandCostStat.total_stolosio_queue_ms
                        + excluded.total_stolosio_queue_ms
                    ),
                    "attributed_browser_time_ms": (
                        ProviderCommandCostStat.attributed_browser_time_ms
                        + excluded.attributed_browser_time_ms
                    ),
                    "attributed_cost_units": (
                        ProviderCommandCostStat.attributed_cost_units
                        + excluded.attributed_cost_units
                    ),
                    "first_seen_at": func.least(
                        ProviderCommandCostStat.first_seen_at,
                        excluded.first_seen_at,
                    ),
                    "last_seen_at": func.greatest(
                        ProviderCommandCostStat.last_seen_at,
                        excluded.last_seen_at,
                    ),
                },
            )
        )

    @staticmethod
    async def _project_cost(
        database: AsyncSession,
        attempt: AcquisitionAttempt | None,
    ) -> None:
        if (
            attempt is None
            or attempt.domain_id is None
            or attempt.modeled_cost_units is None
            or attempt.cost_projected
        ):
            return
        await database.execute(
            insert(DomainProviderCostStat)
            .values(
                domain_id=attempt.domain_id,
                provider=attempt.provider,
                observed_attempt_count=1,
                total_cost_units=attempt.modeled_cost_units,
            )
            .on_conflict_do_update(
                index_elements=[
                    DomainProviderCostStat.domain_id,
                    DomainProviderCostStat.provider,
                ],
                set_={
                    "observed_attempt_count": (DomainProviderCostStat.observed_attempt_count + 1),
                    "total_cost_units": (
                        DomainProviderCostStat.total_cost_units
                        + attempt.modeled_cost_units
                    ),
                },
            )
        )
        attempt.cost_projected = True
