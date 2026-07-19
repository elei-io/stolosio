import pytest

from backend.proxy.network_policy import (
    NetworkPolicy,
    NetworkPolicyRepository,
    blocked_url_patterns,
    domain_matches_pattern,
    normalize_domain_patterns,
)


def test_domain_patterns_are_normalized_deduplicated_and_matched() -> None:
    patterns = normalize_domain_patterns(
        [" *.DoubleClick.NET. ", "ads.example", "*.doubleclick.net"]
    )

    assert patterns == ("*.doubleclick.net", "ads.example")
    assert domain_matches_pattern("img.doubleclick.net", patterns[0])
    assert domain_matches_pattern("deep.img.doubleclick.net", patterns[0])
    assert not domain_matches_pattern("doubleclick.net", patterns[0])
    assert domain_matches_pattern("ads.example", patterns[1])
    assert not domain_matches_pattern("sub.ads.example", patterns[1])
    assert blocked_url_patterns(patterns) == (
        "*://*.doubleclick.net/*",
        "*://ads.example/*",
    )


@pytest.mark.parametrize(
    "pattern",
    ["*", "*doubleclick.net", "https://doubleclick.net", "doubleclick.net/path", ""],
)
def test_invalid_domain_patterns_are_rejected(pattern: str) -> None:
    with pytest.raises(ValueError, match="domain pattern"):
        normalize_domain_patterns([pattern])


@pytest.mark.asyncio
async def test_network_policy_reads_are_cached_in_process() -> None:
    class Database:
        calls = 0

        async def get(self, model, key):
            self.calls += 1
            return type(
                "PolicyRow",
                (),
                {
                    "blocked_domain_patterns": ["ads.example"],
                    "configuration_version": 4,
                },
            )()

    database = Database()

    class SessionContext:
        async def __aenter__(self):
            return database

        async def __aexit__(self, *args):
            return None

    class Sessions:
        def __call__(self):
            return SessionContext()

    repository = NetworkPolicyRepository(Sessions())  # type: ignore[arg-type]

    first = await repository.settings()
    second = await repository.settings()

    assert first == NetworkPolicy(("ads.example",), 4)
    assert second is first
    assert database.calls == 1


@pytest.mark.asyncio
async def test_network_policy_is_durable_and_versioned(database_sessions) -> None:
    repository = NetworkPolicyRepository(database_sessions)
    await repository.ensure_defaults()

    initial = await repository.settings()
    updated = await repository.update(
        ["ads.example", "*.DoubleClick.NET", "ads.example"]
    )
    reloaded = await repository.settings()

    assert initial.blocked_domain_patterns == ()
    assert initial.configuration_version == 1
    assert updated.blocked_domain_patterns == ("*.doubleclick.net", "ads.example")
    assert updated.configuration_version == 2
    assert reloaded == updated
