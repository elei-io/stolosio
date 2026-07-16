from dataclasses import dataclass

from nats.aio.client import Client as NatsClient
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError

from backend.events import SessionEvent

EVENT_STREAM = "HARBOR_EVENTS"
EVENT_SUBJECT = "harbor.v1.events.session.*"


@dataclass(frozen=True, slots=True)
class EventStreamSettings:
    max_age_seconds: int
    max_bytes: int
    max_message_bytes: int
    duplicate_window_seconds: int
    replicas: int


class JetStreamEventPublisher:
    def __init__(self, jetstream: JetStreamContext) -> None:
        self._jetstream = jetstream

    @classmethod
    async def start(
        cls,
        client: NatsClient,
        settings: EventStreamSettings,
    ) -> "JetStreamEventPublisher":
        jetstream = client.jetstream()
        config = api.StreamConfig(
            name=EVENT_STREAM,
            subjects=[EVENT_SUBJECT],
            retention=api.RetentionPolicy.LIMITS,
            storage=api.StorageType.FILE,
            discard=api.DiscardPolicy.NEW,
            max_age=settings.max_age_seconds,
            max_bytes=settings.max_bytes,
            max_msg_size=settings.max_message_bytes,
            duplicate_window=settings.duplicate_window_seconds,
            num_replicas=settings.replicas,
        )
        try:
            await jetstream.stream_info(EVENT_STREAM)
        except NotFoundError:
            await jetstream.add_stream(config=config)
        else:
            await jetstream.update_stream(config=config)
        return cls(jetstream)

    async def publish(self, event: SessionEvent) -> None:
        await self._jetstream.publish(
            event.subject,
            event.to_json(),
            stream=EVENT_STREAM,
            headers={"Nats-Msg-Id": str(event.event_id)},
        )
