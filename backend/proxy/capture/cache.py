from dataclasses import asdict
from datetime import UTC, datetime

from pagecapture.cache import MethodEntry, record_comparison
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import CaptureMethodCacheEntry


class PostgresMethodCache:
    """Atomically update exact and pattern evidence in a consistent lock order."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, key: str) -> MethodEntry | None:
        async with self._sessions() as database:
            row = await database.get(CaptureMethodCacheEntry, key)
            return MethodEntry(**row.entry) if row is not None else None

    async def record(
        self, keys: tuple[str, str], sufficient: bool, http_bytes: int, now: float, ttl_s: float
    ) -> None:
        async with self._sessions.begin() as database:
            for key in sorted(keys):
                await database.execute(
                    insert(CaptureMethodCacheEntry)
                    .values(
                        key=key,
                        entry=asdict(MethodEntry()),
                        last_seen=datetime.fromtimestamp(now, UTC),
                    )
                    .on_conflict_do_nothing(index_elements=[CaptureMethodCacheEntry.key])
                )
                row = await database.scalar(
                    select(CaptureMethodCacheEntry)
                    .where(CaptureMethodCacheEntry.key == key)
                    .with_for_update()
                )
                assert row is not None
                entry = record_comparison(
                    MethodEntry(**row.entry), sufficient, http_bytes, now, ttl_s
                )
                row.entry = asdict(entry)
                row.last_seen = datetime.fromtimestamp(now, UTC)
