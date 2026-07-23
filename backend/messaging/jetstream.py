from dataclasses import dataclass

from nats.aio.client import Client as NatsClient
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import BadRequestError, NotFoundError

from backend.events import SessionEvent

EVENT_STREAM = "HARBOR_EVENTS"
EVENT_SUBJECT = "harbor.v1.events.session.*"
DEAD_LETTER_STREAM = "HARBOR_DEAD_LETTERS"
DEAD_LETTER_SUBJECT = "harbor.v1.dead_letters"
RECORDER_CONSUMER = "harbor-recorder-v1"


@dataclass(frozen=True, slots=True)
class EventStreamSettings:
    max_age_seconds: int
    max_bytes: int
    max_message_bytes: int
    duplicate_window_seconds: int
    replicas: int


@dataclass(frozen=True, slots=True)
class HarborTopology:
    event_stream_created: bool


def event_stream_config(settings: EventStreamSettings) -> api.StreamConfig:
    return api.StreamConfig(
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


def dead_letter_stream_config(
    settings: EventStreamSettings,
    *,
    max_bytes: int,
) -> api.StreamConfig:
    return api.StreamConfig(
        name=DEAD_LETTER_STREAM,
        subjects=[DEAD_LETTER_SUBJECT],
        retention=api.RetentionPolicy.LIMITS,
        storage=api.StorageType.FILE,
        discard=api.DiscardPolicy.OLD,
        max_msgs=10_000,
        max_age=30 * 86_400,
        max_bytes=max_bytes,
        num_replicas=settings.replicas,
    )


def recorder_consumer_config() -> api.ConsumerConfig:
    return api.ConsumerConfig(
        durable_name=RECORDER_CONSUMER,
        name=RECORDER_CONSUMER,
        deliver_policy=api.DeliverPolicy.ALL,
        ack_policy=api.AckPolicy.EXPLICIT,
        filter_subject=EVENT_SUBJECT,
    )


async def _ensure_stream(
    jetstream: JetStreamContext,
    config: api.StreamConfig,
) -> bool:
    try:
        await jetstream.stream_info(config.name)
    except NotFoundError:
        try:
            await jetstream.add_stream(config=config)
        except BadRequestError:
            # Another Harbor process may have observed the same empty account.
            await jetstream.stream_info(config.name)
        else:
            return True
    await jetstream.update_stream(config=config)
    return False


async def ensure_harbor_topology(
    jetstream: JetStreamContext,
    settings: EventStreamSettings,
    *,
    dead_letter_max_bytes: int,
) -> HarborTopology:
    event_stream_created = await _ensure_stream(
        jetstream,
        event_stream_config(settings),
    )
    await _ensure_stream(
        jetstream,
        dead_letter_stream_config(
            settings,
            max_bytes=dead_letter_max_bytes,
        ),
    )
    return HarborTopology(event_stream_created=event_stream_created)


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
        await _ensure_stream(jetstream, event_stream_config(settings))
        return cls(jetstream)

    @classmethod
    def connected(cls, client: NatsClient) -> "JetStreamEventPublisher":
        return cls(client.jetstream())

    async def publish(self, event: SessionEvent) -> None:
        await self._jetstream.publish(
            event.subject,
            event.to_json(),
            stream=EVENT_STREAM,
            headers={"Nats-Msg-Id": str(event.event_id)},
        )
