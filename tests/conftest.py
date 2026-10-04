from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.session import Base
from backend.settings import Settings


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-integration-services",
        action="store_true",
        help="Fail instead of skipping tests when PostgreSQL or NATS is unavailable",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    report = yield
    if (
        item.config.getoption("--require-integration-services")
        and report.skipped
        and "integration service is not available" in str(report.longrepr)
    ):
        report.outcome = "failed"
        report.longrepr = "Required integration service is unavailable: " + str(report.longrepr)
    return report


@pytest_asyncio.fixture
async def database_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = Settings()
    admin_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    schema = f"stolosio_test_{uuid4().hex}"
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception:
        await admin_engine.dispose()
        pytest.skip("Postgres integration service is not available")

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin_engine.dispose()
