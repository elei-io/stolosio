import pytest
from nats.js import api
from nats.js.errors import NotFoundError

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
