import asyncio
import time

import pytest

from pagecapture.cache import MemoryMethodCache, MethodPolicy, SqliteMethodCache, url_keys
from pagecapture.config import Settings


@pytest.mark.parametrize("index", [0, 1])
def test_cache_keys_preserve_origins(index):
    urls = [
        "http://example.test/page",
        "https://example.test/page",
        "https://example.test:8443/page",
        "https://www.example.test/page",
        "https://[2001:db8::1]/page",
        "https://[2001:db8::2]/page",
    ]
    assert len({url_keys(url)[index] for url in urls}) == len(urls)
    assert url_keys("https://example.test:443/page")[index] == url_keys(urls[1])[index]


def test_exact_keys_preserve_query_values_and_order():
    urls = [
        "https://example.test/page?a=1%26b%3D2",
        "https://example.test/page?a=1&b=2",
        "https://example.test/page?b=2&a=1",
        "https://example.test/page?a=1&a=2",
        "https://example.test/page?a=2&a=1",
    ]
    assert len({url_keys(url)[0] for url in urls}) == len(urls)
    assert url_keys("https://example.test/page?a%26b=1")[1] != url_keys("https://example.test/page?a=1&b=1")[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["memory", "sqlite"])
async def test_contradictions_survive_success_and_prevent_pattern_fallback(storage, tmp_path):
    cache = MemoryMethodCache() if storage == "memory" else SqliteMethodCache(tmp_path / "cache.db")
    policy = MethodPolicy(cache, Settings())
    url = "https://example.test/products/123"
    for _ in range(4):
        await policy.record(url, True, 1000)
    await policy.record(url, False, 1000)
    await policy.record(url, True, 1000)
    exact = await cache.get(url_keys(url)[0])
    assert exact.comparisons == 6 and exact.contradictions == 1
    assert await policy.http_sufficient(url, 1000) is None
    assert await policy.http_sufficient("https://example.test/products/456", 1000) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["memory", "sqlite"])
async def test_expired_evidence_can_be_relearned(storage, tmp_path):
    cache = MemoryMethodCache() if storage == "memory" else SqliteMethodCache(tmp_path / "cache.db")
    keys = url_keys("https://example.test/page")
    await cache.record(keys, False, 1000, time.time() - 100, 10)
    await cache.record(keys, True, 1000, time.time(), 10)
    entry = await cache.get(keys[0])
    assert entry.comparisons == 1 and entry.contradictions == 0


@pytest.mark.asyncio
async def test_sqlite_independent_connections_retain_every_comparison(tmp_path):
    caches = [SqliteMethodCache(tmp_path / "cache.db") for _ in range(8)]
    keys = url_keys("https://example.test/page")
    await asyncio.gather(
        *(
            asyncio.to_thread(asyncio.run, cache.record(keys, i != 0, 1000, time.time(), 100))
            for i, cache in enumerate(caches)
        )
    )
    for key in keys:
        entry = await caches[0].get(key)
        assert entry.comparisons == 8 and entry.sufficient == 7 and entry.contradictions == 1
