from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import (
    AcquisitionAttempt,
    Domain,
    GatewaySession,
    SessionDomain,
)
from backend.proxy.session_queries import SessionFilters, SessionQueryService


@pytest.mark.asyncio
async def test_session_queries_return_summaries_and_redacted_detail(
    database_sessions,
) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        domain = Domain(
            hostname="example.test",
            first_seen_at=now,
            last_seen_at=now,
        )
        database.add(domain)
        await database.flush()
        session = GatewaySession(
            id="00000000-0000-0000-0000-000000000001",
            owner_id="owner",
            lease_token="lease",
            client_reference="00000000-0000-0000-0000-000000000002",
            requested_settings={"proxy": "secret", "locale": "en-US"},
            state="closed",
            created_at=now - timedelta(seconds=10),
            admitted_at=now - timedelta(seconds=9),
            opened_at=now - timedelta(seconds=8),
            closed_at=now,
        )
        database.add(session)
        await database.flush()
        attempt = AcquisitionAttempt(
            id="00000000-0000-0000-0000-000000000003",
            session_id=session.id,
            ordinal=1,
            provider="http",
            resolved_settings={"proxy": "secret"},
            setting_sources={"provider": "automatic"},
            state="closed",
            selection_reason="cheapest_eligible",
            modeled_cost_units=2,
            created_at=now - timedelta(seconds=9),
            finished_at=now,
        )
        database.add_all(
            [
                attempt,
                SessionDomain(
                    session_id=session.id,
                    domain_id=domain.id,
                    first_seen_at=now,
                    last_seen_at=now,
                ),
            ]
        )

    service = SessionQueryService(database_sessions)
    page = await service.sessions(SessionFilters(provider="http"))
    detail = await service.session(session.id)

    assert page.sessions[0]["providers"] == ["http"]
    assert page.sessions[0]["domains"] == [{"id": domain.id, "hostname": "example.test"}]
    assert page.sessions[0]["duration_seconds"] == 10
    assert detail is not None
    assert detail["requested_setting_keys"] == ["locale", "proxy"]
    assert detail["attempts"][0]["resolved_setting_keys"] == ["proxy"]
    assert "requested_settings" not in detail
    assert "resolved_settings" not in detail["attempts"][0]


@pytest.mark.asyncio
async def test_session_queries_filter_and_paginate(database_sessions) -> None:
    now = datetime.now(UTC)
    async with database_sessions.begin() as database:
        for index in range(3):
            database.add(
                GatewaySession(
                    id=f"00000000-0000-0000-0000-{index:012d}",
                    owner_id="owner",
                    lease_token=f"lease-{index}",
                    requested_settings={},
                    state="failed" if index == 0 else "closed",
                    created_at=now + timedelta(seconds=index),
                )
            )

    service = SessionQueryService(database_sessions)
    first = await service.sessions(SessionFilters(state="closed"), limit=1)
    second = await service.sessions(
        SessionFilters(state="closed"), before=first.next_cursor, limit=1
    )

    assert len(first.sessions) == 1
    assert first.next_cursor is not None
    assert len(second.sessions) == 1
    assert second.next_cursor is None
