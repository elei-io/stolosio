import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import NetworkPolicyConfiguration

_MAX_BLOCKED_DOMAIN_PATTERNS = 1_000


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    blocked_domain_patterns: tuple[str, ...]
    configuration_version: int


def normalize_domain_pattern(value: str) -> str:
    pattern = value.strip().lower().rstrip(".")
    wildcard = pattern.startswith("*.")
    hostname = pattern[2:] if wildcard else pattern
    if not hostname or "*" in hostname or "://" in hostname or "/" in hostname or ":" in hostname:
        raise ValueError(
            "Blocked domain patterns must be hostnames or use one leading '*.' wildcard"
        )
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(f"Invalid blocked domain pattern: {value!r}") from error
    if len(ascii_hostname) > 253:
        raise ValueError(f"Blocked domain pattern is too long: {value!r}")
    for label in ascii_hostname.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
        ):
            raise ValueError(f"Invalid blocked domain pattern: {value!r}")
    return f"*.{ascii_hostname}" if wildcard else ascii_hostname


def normalize_domain_patterns(values: list[str]) -> tuple[str, ...]:
    if len(values) > _MAX_BLOCKED_DOMAIN_PATTERNS:
        raise ValueError(
            f"At most {_MAX_BLOCKED_DOMAIN_PATTERNS} blocked domain patterns are allowed"
        )
    normalized = {normalize_domain_pattern(value) for value in values}
    return tuple(sorted(normalized))


def domain_matches_pattern(hostname: str, pattern: str) -> bool:
    normalized_hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return normalized_hostname.endswith(f".{suffix}")
    return normalized_hostname == pattern


def blocked_url_patterns(domain_patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"*://{pattern}/*" for pattern in domain_patterns)


class NetworkPolicyRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        cache_ttl_seconds: float = 60,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("Network policy cache TTL must be positive")
        self._sessions = sessions
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached: NetworkPolicy | None = None
        self._cache_expires_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def ensure_defaults(self) -> None:
        async with self._cache_lock:
            async with self._sessions.begin() as database:
                await database.execute(
                    insert(NetworkPolicyConfiguration)
                    .values(
                        key="global",
                        blocked_domain_patterns=[],
                        configuration_version=1,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[NetworkPolicyConfiguration.key]
                    )
                )
                row = await database.get(NetworkPolicyConfiguration, "global")
                assert row is not None
                value = NetworkPolicy(
                    tuple(row.blocked_domain_patterns),
                    row.configuration_version,
                )
            self._set_cache(value, monotonic())

    async def settings(self) -> NetworkPolicy:
        now = monotonic()
        if self._cached is not None and now < self._cache_expires_at:
            return self._cached
        async with self._cache_lock:
            now = monotonic()
            if self._cached is not None and now < self._cache_expires_at:
                return self._cached
            async with self._sessions() as database:
                row = await database.get(NetworkPolicyConfiguration, "global")
            value = (
                NetworkPolicy((), 1)
                if row is None
                else NetworkPolicy(
                    tuple(row.blocked_domain_patterns),
                    row.configuration_version,
                )
            )
            self._set_cache(value, now)
            return value

    async def update(self, blocked_domain_patterns: list[str]) -> NetworkPolicy:
        normalized = normalize_domain_patterns(blocked_domain_patterns)
        async with self._cache_lock:
            async with self._sessions.begin() as database:
                row = await database.get(
                    NetworkPolicyConfiguration,
                    "global",
                    with_for_update=True,
                )
                if row is None:
                    raise RuntimeError("Network policy is not initialized")
                row.blocked_domain_patterns = list(normalized)
                row.configuration_version += 1
                row.updated_at = datetime.now(UTC)
                value = NetworkPolicy(normalized, row.configuration_version)
            self._set_cache(value, monotonic())
            return value

    def _set_cache(self, value: NetworkPolicy, cached_at: float) -> None:
        self._cached = value
        self._cache_expires_at = cached_at + self._cache_ttl_seconds
