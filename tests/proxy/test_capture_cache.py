import asyncio
import time

import pytest
from pagecapture.cache import MethodPolicy, url_keys
from pagecapture.config import Settings

from backend.proxy.capture.cache import PostgresMethodCache


@pytest.mark.asyncio
async def test_postgres_cache_retains_concurrent_comparisons(database_sessions):
    cache = PostgresMethodCache(database_sessions)
    keys = url_keys("https://example.test/products/123")
    await asyncio.gather(*(cache.record(keys, i != 0, 1000, time.time(), 100) for i in range(12)))
    for key in keys:
        entry = await cache.get(key)
        assert entry.comparisons == 12
        assert entry.sufficient == 11 and entry.contradictions == 1
    assert (
        await MethodPolicy(cache, Settings()).http_sufficient(
            "https://example.test/products/123", 1000
        )
        is None
    )
