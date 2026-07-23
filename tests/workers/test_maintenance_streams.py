import pytest
from nats.js import api
from nats.js.errors import NotFoundError

from backend.messaging.jetstream import (
    DEAD_LETTER_STREAM,
    EVENT_STREAM,
    EventStreamSettings,
    ensure_harbor_topology,
)
from backend.settings import settings
from backend.workers.maintenance.__main__ import _ensure_dead_letters


class MissingStream:
    def __init__(self) -> None:
        self.config: api.StreamConfig | None = None

    async def stream_info(self, name: str) -> None:
        raise NotFoundError(code=404, err_code=10059, description=f"{name} not found")

    async def add_stream(self, *, config: api.StreamConfig) -> None:
        self.config = config


@pytest.mark.asyncio
async def test_dead_letter_stream_has_bounded_storage() -> None:
    jetstream = MissingStream()

    await _ensure_dead_letters(jetstream)

    assert jetstream.config is not None
    assert jetstream.config.max_bytes == settings.jetstream_dead_letter_max_bytes


class EmptyAccount:
    def __init__(self) -> None:
        self.streams: dict[str, api.StreamConfig] = {}

    async def stream_info(self, name: str) -> None:
        if name not in self.streams:
            raise NotFoundError(code=404, err_code=10059, description=f"{name} not found")

    async def add_stream(self, *, config: api.StreamConfig) -> None:
        self.streams[config.name] = config

    async def update_stream(self, *, config: api.StreamConfig) -> None:
        self.streams[config.name] = config


@pytest.mark.asyncio
async def test_empty_account_is_reconciled_by_harbor() -> None:
    jetstream = EmptyAccount()

    result = await ensure_harbor_topology(
        jetstream,
        EventStreamSettings(
            max_age_seconds=60,
            max_bytes=1024,
            max_message_bytes=512,
            duplicate_window_seconds=30,
            replicas=3,
        ),
        dead_letter_max_bytes=256,
    )

    assert result.event_stream_created is True
    assert set(jetstream.streams) == {EVENT_STREAM, DEAD_LETTER_STREAM}
    assert jetstream.streams[EVENT_STREAM].num_replicas == 3

    result = await ensure_harbor_topology(
        jetstream,
        EventStreamSettings(
            max_age_seconds=60,
            max_bytes=1024,
            max_message_bytes=512,
            duplicate_window_seconds=30,
            replicas=3,
        ),
        dead_letter_max_bytes=256,
    )

    assert result.event_stream_created is False
