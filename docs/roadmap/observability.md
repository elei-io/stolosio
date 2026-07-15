# Evidence and Observability Foundation

Status: planned
Roadmap package: 2
Depends on: [Dependable Browser Gateway](gateway.md)
Target: a factual session event backbone, durable evidence, and bounded operational metrics

## Purpose

Harbor must record what happened before it can learn which acquisition choices are
correct or economical. This package introduces one normalized session event contract
used by several consumers:

- Core NATS subscribers receive live DEBUG events.
- JetStream retains the same publications for replay and independent durable consumers.
- A maintenance worker records filtered evidence and query projections in PostgreSQL.
- Prometheus exposes bounded operational measurements.
- A JSON fleet endpoint presents the same live provider snapshot used by metrics.

The central rule is:

> Proxy code produces facts once. Persistence, DEBUG, metrics, analytics, and future
> user interfaces consume different views of those same facts.

This package records evidence. It does not infer that a domain needs more stealth,
should rotate its IP, can safely use HTTP, or should change provider.

## Goals

- Define a small versioned event envelope shared by lifecycle, provider-attempt,
  command, and browser-observation events.
- Publish all normalized events on session-addressable NATS subjects captured by one
  JetStream stream.
- Preserve the gateway invariant that lifecycle state and its durable event row commit
  in one PostgreSQL transaction.
- Reliably forward lifecycle event rows to JetStream using those rows as a transactional
  outbox.
- Record every provider acquisition attempt, including attempts that fail before the
  downstream WebSocket is accepted.
- Record every valid downstream CDP command and its success, CDP error, unsupported
  result, or interruption.
- Normalize a small initial subset of provider evidence across Chromium, Browserless,
  Lightpanda, and Camoufox.
- Persist JetStream observations idempotently to PostgreSQL.
- Maintain domain and domain-command projections without interpreting them.
- Bound JetStream and PostgreSQL detail retention.
- Expose bounded Prometheus metrics without domain, URL, session, or arbitrary error
  labels.
- Expose a JSON fleet snapshot backed by the same query service as Prometheus fleet
  gauges.
- Keep sensitive material out of NATS and PostgreSQL by default.

## Non-goals

- Correctness classification, confidence scores, or routing recommendations.
- Provider fallback, HTTP-only execution, browser promotion, or proxy selection.
- A public downstream DEBUG protocol. Package 2 proves the internal event backbone;
  public authentication, cursors, filtering, and delivery limits come later.
- Persisting raw CDP, Playwright, Juggler, WebSocket, response-body, or page-content
  payloads.
- Capturing every subresource request. The initial observation slice is limited to
  main-document navigation.
- Using Prometheus for domain or session analytics.
- Kubernetes HPA configuration. This package produces the metrics consumed by the
  following scaling package.
- Machine learning, anomaly detection, CAPTCHA inference, or other derived conclusions.

## System shape

```text
                              Core NATS live subscribers
                                         ^
                                         |
Gateway / adapters ---> harbor.v1.events.session.<session_id>
       |                                 |
       |                                 v
       |                         JetStream HARBOR_EVENTS
       |                                 |
       v                                 v
PostgreSQL lifecycle rows         maintenance recorder
       |                                 |
       +--- outbox publisher ------------+----> PostgreSQL observations/projections
```

PostgreSQL remains the transactional source of truth for gateway lifecycle and
capacity. NATS is not used to decide whether a session owns a browser slot.

A JetStream publish is also a normal NATS subject publication. Live Core NATS
subscribers receive it immediately while the configured stream stores it. Harbor does
not publish separate live and durable copies.

## Event contract

### Envelope

Every publication uses this application contract:

```python
@dataclass(frozen=True, slots=True)
class SessionEvent:
    event_id: UUID
    schema_version: int
    event_type: str
    session_id: UUID
    occurred_at: datetime
    provider: ProviderName | None
    attempt_id: UUID | None
    payload: Mapping[str, JsonValue]
```

| Field | Meaning |
| --- | --- |
| `event_id` | Stable UUID generated once by the event producer |
| `schema_version` | Integer envelope version; initially `1` |
| `event_type` | Stable dotted event name from the checked-in registry |
| `session_id` | Harbor logical session UUID |
| `occurred_at` | UTC timestamp describing when Harbor observed the fact |
| `provider` | Concrete provider when the fact concerns one |
| `attempt_id` | Provider-attempt UUID when applicable |
| `payload` | Event-specific normalized JSON object |

`event_id` is sent as the JetStream `Nats-Msg-Id` header. A retry therefore has the
same application identity and can be deduplicated by JetStream during its duplicate
window and by PostgreSQL forever while the event row is retained.

There is no fabricated total order across services. Events from one live proxy
connection are published in observation order. Historical timelines sort by
`occurred_at` and use the stable event ID as a tie-breaker. Command IDs and attempt IDs
carry causal relationships where ordering matters.

### NATS subjects

All session evidence uses one subject shape:

```text
harbor.v1.events.session.<session_id>
```

Examples:

```text
harbor.v1.events.session.6c68b988-3a89-4ff1-bf85-a344c3111d44
```

The event type remains in the envelope rather than the subject. This keeps live DEBUG
subscription session-addressable without multiplying subject conventions. Internal
all-session consumers subscribe to `harbor.v1.events.session.*`.

Capacity wakeups remain separate ephemeral subjects owned by package 1:

```text
harbor.v1.capacity.<provider>
```

They are not captured in the event stream because PostgreSQL polling is the durable
fallback and replaying an old capacity wakeup has no meaning.

### Initial event inventory

Only registered event types may be published. Adding one requires a typed payload,
normalization tests, persistence tests, and a documentation update.

#### Session lifecycle

```text
session.requested
session.queued
session.acquiring
session.connected
session.closing
session.closed
session.failed
```

Lifecycle events originate from the PostgreSQL `session_events` rows written by the
gateway transaction. `session.requested` contains requested settings, resolved settings,
and setting sources. Later transitions contain the stable reason when applicable.

#### Provider attempts

```text
attempt.started
attempt.connected
attempt.failed
attempt.closed
```

The gateway creates one UUID4 `attempt_id` immediately before
`ProviderAdapter.acquire()`. Package 2 creates exactly one attempt per admitted session.
The separate identity allows a later fallback package to add attempts without changing
the session contract.

Attempt payload fields are:

```text
duration_ms
reason
```

Reasons are stable Harbor codes, not exception messages.

#### CDP commands

```text
command.received
command.succeeded
command.failed
command.interrupted
```

Command payload fields are:

```text
command_id
method
domain
duration_ms
reason
cdp_error_code
```

- `command.received` follows structural validation and precedes capability enforcement.
- Unsupported methods fail with `unsupported_command` and numeric CDP error `-32601`.
- Upstream CDP errors use `provider_command_error`; provider error text is not stored.
- Pending commands become interrupted when either socket closes.
- `domain` is the normalized current main-frame hostname known when the command is
  received, or null before navigation establishes one.

The active relay holds this process-local correlation map:

```text
CDP command ID -> method, domain, monotonic start time, target session ID
```

It is bounded by unanswered commands and discarded at connection close.

#### Initial browser observations

```text
navigation.requested
navigation.redirected
navigation.response
navigation.failed
page.dom_content_loaded
page.loaded
page.crashed
provider.disconnected
```

The initial slice observes main-document navigation only. Subresources, console
messages, cookie changes, downloads, dialogs, secondary frames, and response bodies
remain future additions evaluated from real provider evidence one at a time.

Payloads contain applicable values from:

```text
url
previous_url
status
mime_type
resource_type
selected_headers
error_type
duration_ms
```

An event reports only what the provider explicitly supplied. Harbor does not synthesize
`page.loaded` for a provider without equivalent evidence and does not infer a CAPTCHA
from status, title, markup, or redirects.

## Filtering and normalization

Filtering occurs before publication. Neither JetStream nor a downstream consumer can
recover material that should never have entered the event system.

Initial rules are:

- Never publish bodies, HTML, screenshots, evaluated source, remote-object values,
  form data, cookies, authorization headers, proxy credentials, raw WebSocket payloads,
  or raw protocol messages.
- Store command method and numeric CDP error code, never provider error text.
- URLs remove user information, fragments, and query strings. Scheme, normalized host,
  explicit non-default port, and path remain.
- Hosts become lowercase ASCII IDNA with trailing dots removed.
- Response headers use this allowlist: `content-type`, `content-length`, `location`, and
  `retry-after`.
- `location` values pass through the same URL sanitizer.
- Domains derive only from valid HTTP or HTTPS navigation URLs.

Passthrough CDP providers and the Camoufox facade publish through the same typed
interface:

```python
class EventPublisher(Protocol):
    async def publish(self, event: SessionEvent) -> None: ...
```

The transport extracts approved CDP observations. Camoufox emits equivalent facts from
the native Playwright evidence it already handles. A provider-specific fact must be
explicitly identified rather than presented as common evidence.

The proxy never waits for an event consumer to decide how to execute an active command.
Consumers are observational, not part of the browser command path.

## JetStream design

### Stream

Package 2 creates one stream idempotently at worker startup:

| Setting | Value |
| --- | --- |
| Name | `HARBOR_EVENTS` |
| Subjects | `harbor.v1.events.session.*` |
| Storage | file |
| Retention | limits |
| Discard policy | discard new |
| Maximum age | 24 hours by default |
| Maximum bytes | 10 GiB by default |
| Maximum message size | 256 KiB |
| Duplicate window | 10 minutes |
| Replicas | 1 locally; environment-configurable |

Limits retention is required because independent consumers need their own replay and
acknowledgement positions. Work-queue retention is not used; it would make consumers
compete for one copy instead of each receiving the evidence they requested.

Discard-new is deliberate. When the configured byte limit is reached, the publisher
gets an error and Harbor exposes data loss pressure instead of silently deleting the
oldest evidence before durable consumers record it.

Configuration is environment backed:

```text
NATS_URL
JETSTREAM_EVENT_MAX_AGE_SECONDS
JETSTREAM_EVENT_MAX_BYTES
JETSTREAM_EVENT_MAX_MESSAGE_BYTES
JETSTREAM_EVENT_DUPLICATE_WINDOW_SECONDS
JETSTREAM_EVENT_REPLICAS
```

### Publication

Observation publishers use acknowledged JetStream publication. A publish succeeds only
after the server confirms storage. The stable event ID is reused on retry.

Required observation publication is awaited. Because the initial evidence set is
small, package 2 does not introduce a batching queue inside API processes. If later
subresource evidence requires batching, that gets an explicit bounded-buffer and loss
policy rather than an unbounded task collection.

A transient observation publication failure:

- increments a bounded failure metric;
- logs the event type and session ID without payload;
- does not fabricate an event;
- does not terminate an otherwise healthy browser session in this package.

### Lifecycle transactional outbox

Lifecycle correctness remains PostgreSQL-first:

1. The gateway changes `gateway_sessions` and inserts the lifecycle `session_events`
   row in one transaction.
2. After commit, the API attempts an acknowledged JetStream publish for low latency.
3. On success it marks the row published.
4. If the API dies or NATS is unavailable, the maintenance outbox publisher later
   selects unpublished lifecycle rows with `FOR UPDATE SKIP LOCKED`.
5. It publishes using the row's stable event ID and then marks it published.

The database row gains:

```text
event_id UUID unique
schema_version integer
attempt_id UUID nullable
payload JSONB
published_at timestamptz nullable
```

State and durable evidence therefore cannot diverge. JetStream may receive a retry, but
its duplicate window and consumer idempotency make that harmless.

## Maintenance worker

Compose adds one process:

```text
maintenance -> uv run python -m backend.workers.maintenance
```

It contains three small loops:

- `LifecyclePublisher`: forwards unpublished PostgreSQL lifecycle rows to JetStream.
- `EventRecorder`: consumes the durable recorder consumer into PostgreSQL projections.
- `RetentionJob`: removes expired detailed evidence in bounded batches.

The process also exposes an internal Prometheus endpoint for its own lag, publish,
recording, and retention metrics. Multiple maintenance replicas are safe:

- PostgreSQL outbox rows use `FOR UPDATE SKIP LOCKED`.
- The JetStream recorder uses one shared durable pull consumer.
- PostgreSQL inserts use the event ID as their idempotency key.

### Recorder consumer

The durable consumer is:

| Setting | Value |
| --- | --- |
| Stream | `HARBOR_EVENTS` |
| Durable name | `harbor-recorder-v1` |
| Filter | `harbor.v1.events.session.*` |
| Delivery | pull |
| Ack policy | explicit |
| Ack wait | 60 seconds |
| Maximum deliveries | 5 |
| Start | all available events |

For each batch the recorder:

1. Fetches up to 250 events.
2. Validates envelope version, event type, and payload.
3. Starts one PostgreSQL transaction.
4. Inserts unseen events by `event_id`.
5. Applies projections only for newly inserted rows.
6. Commits.
7. Acknowledges committed JetStream messages.

It never acknowledges before commit. Redelivery after commit is harmless because an
existing event ID skips every additive projection in the same transaction.

Malformed events are negatively acknowledged until maximum delivery. The final failed
delivery is published to a bounded `HARBOR_DEAD_LETTERS` stream with only its event ID,
subject, schema version, and sanitized validation reason; the original payload is not
copied blindly.

## PostgreSQL model

Package 2 extends the package 1 schema through Alembic.

### `gateway_sessions`

The existing row remains the logical session summary. It already contains requested
and resolved settings, setting sources, lifecycle state, timestamps, lease information,
and terminal reason. Package 2 adds no second session-summary table.

### `session_attempts`

| Column | Meaning |
| --- | --- |
| `id` | Attempt UUID primary key |
| `session_id` | Indexed session UUID |
| `ordinal` | One-based attempt position; unique per session |
| `provider` | Concrete provider |
| `state` | Started, connected, failed, or closed |
| `terminal_reason` | Stable reason when applicable |
| `started_at` | Attempt start |
| `connected_at` | Upstream connected time |
| `finished_at` | Failed or closed time |

Package 2 writes ordinal one. Later fallback work may add later ordinals.

### `session_events`

The package 1 lifecycle table becomes the common filtered event table:

| Column | Meaning |
| --- | --- |
| `event_id` | UUID idempotency key |
| `schema_version` | Envelope version |
| `session_id` | Indexed session UUID |
| `attempt_id` | Indexed nullable attempt UUID |
| `event_type` | Indexed dotted event name |
| `provider` | Concrete provider when applicable |
| `occurred_at` | Indexed UTC timestamp |
| `payload` | Normalized JSONB payload |
| `published_at` | Lifecycle outbox publication time, nullable |

Lifecycle rows originate in PostgreSQL and observations originate in JetStream. Both
converge in this table under the same event contract.

### Domain projections

`domains` stores normalized hostname, first-seen time, last-seen time, and distinct
session count. `session_domains` prevents redelivery from incrementing that count.

`domain_command_stats` stores domain, method, command count, distinct session count,
first-seen time, and last-seen time. The command event carries the domain known at
receipt time, so out-of-order delivery cannot associate it with a later navigation.

These tables record usage only. They do not conclude that HTTP would have worked or
that a command required a browser.

### Retention

Default retention is:

| Data | Default |
| --- | ---: |
| JetStream event detail | 24 hours |
| PostgreSQL event detail | 30 days |
| Terminal session and attempt summaries | 90 days |
| Inactive domain and command aggregates | 365 days |

The hourly retention job deletes at most 10,000 rows per table per transaction. It does
not use long unbounded deletes. Partitioning remains deferred until measured table and
vacuum behavior justify it.

## Metrics and fleet API

Prometheus contains bounded operational series. PostgreSQL contains exact session,
domain, URL, command, and historical evidence.

Initial metrics are:

```text
harbor_provider_active_sessions{provider}
harbor_provider_queued_sessions{provider}
harbor_provider_capacity{provider}
harbor_provider_oldest_queued_seconds{provider}

harbor_session_acquisitions_total{provider,outcome}
harbor_session_acquisition_duration_seconds{provider}
harbor_sessions_completed_total{provider,outcome}
harbor_provider_failures_total{provider,reason}

harbor_commands_total{provider,method,outcome}
harbor_command_duration_seconds{provider,method,outcome}

harbor_event_publication_failures_total{event_type}
harbor_event_recorder_events_total{outcome}
harbor_event_recorder_lag_seconds
harbor_event_recorder_pending
harbor_event_dead_letters_total{reason}
harbor_retention_deleted_rows_total{table}
harbor_retention_duration_seconds
```

Labels never contain domain, URL, session ID, attempt ID, header values, or arbitrary
exception text. CDP methods come from the approved capability registry; unknown values
map to `other`. Stable reasons not in the metric registry also map to `other`.

One query service owns live provider state:

```python
@dataclass(frozen=True, slots=True)
class ProviderFleetSnapshot:
    provider: ProviderName
    active_sessions: int
    queued_sessions: int
    capacity: int
    oldest_queued_seconds: float
```

It reads unexpired active and queued rows from PostgreSQL plus configured capacity.
Prometheus converts the result to gauges. `GET /v1/fleet/providers` serializes the same
result as JSON. Historical Prometheus rates and quantiles are not reimplemented in the
JSON API.

Every API replica exposes the same PostgreSQL-backed gauges. Prometheus must aggregate
shared gauges with `max` across API instances, never `sum`. Process-local counters and
histograms are summed.

## Internal DEBUG view

`backend/debug/` consumes `SessionEvent`; it does not own publication or persistence.

The initial internal live view subscribes to:

```text
harbor.v1.events.session.<session_id>
```

Historical tests and operator tools query `session_events`. The view presents normalized
fields, never joins analytical conclusions, and truthfully omits evidence a provider
did not supply. This preserves [DEBUG](../DEBUG.md).

Public authentication, resume cursors, slow-consumer policy, and downstream filtering
remain a later package.

## Target package structure

```text
backend/
├── analytics/projections/
├── api/routes/
│   ├── fleet.py
│   └── metrics.py
├── db/
│   ├── migrations/
│   ├── models/
│   └── repositories/
├── debug/
│   └── timeline.py
├── events/
│   ├── contracts.py
│   ├── publisher.py
│   ├── registry.py
│   └── normalization/
├── messaging/
│   ├── capacity.py
│   └── jetstream.py
├── metrics/
│   ├── definitions.py
│   ├── fleet.py
│   └── instrumentation.py
└── workers/maintenance/
    ├── __main__.py
    ├── lifecycle_publisher.py
    ├── recorder.py
    └── retention.py
```

Responsibilities are fixed:

- Gateway and transport act on live connections and publish facts.
- Event registry declares allowed event types and typed payloads.
- JetStream publisher serializes, acknowledges, and retries stable event IDs.
- Lifecycle publisher forwards the PostgreSQL transactional outbox.
- Recorder validates, transactionally persists, projects, and acknowledges evidence.
- DEBUG presents factual events without interpretation.
- Fleet service owns the PostgreSQL query shared by JSON and Prometheus.
- Retention removes expired detail in bounded transactions.

## Failure behavior

### PostgreSQL unavailable

- Gateway admission fails closed as package 1 requires.
- Active heartbeat failure closes the session rather than continuing without ownership.
- Maintenance outbox and recorder pause without acknowledging work.
- Fleet JSON returns `503`; process-local Prometheus metrics remain renderable.

### NATS unavailable

- Session admission and capacity remain correct in PostgreSQL.
- Queued connections use one-second PostgreSQL fallback polling.
- New observation publication failures are counted and logged without payload.
- Lifecycle rows accumulate unpublished in PostgreSQL and publish after recovery.
- Live DEBUG is unavailable during the outage.

### Maintenance crash or duplicate worker

- PostgreSQL `SKIP LOCKED` safely divides outbox rows.
- JetStream redelivers unacknowledged observations.
- Event-ID conflicts prevent duplicate records and projections.

### JetStream retention pressure

- Discard-new makes acknowledged publication fail instead of silently deleting old
  unrecorded events.
- Stream bytes, consumer lag, pending messages, and publication failures alert before
  the limit becomes normal operation.

## Testing strategy

### Unit tests

- Every event validates its exact payload; unknown types and fields fail.
- URL and header sanitization covers credentials, queries, fragments, IDNA, ports,
  invalid hosts, and sensitive headers.
- Command responses correlate to the correct method, domain, and duration.
- Unsupported, provider-rejected, and interrupted commands remain distinct.
- Metric registries map unknown labels to `other`.
- DEBUG timelines contain no interpretation.

### NATS and JetStream integration tests

- Stream creation is idempotent and matches checked-in limits.
- One publication reaches a live subscriber and the durable recorder consumer.
- Stable `Nats-Msg-Id` retries do not create duplicate stream messages within the
  duplicate window.
- Recorder restart replays unacknowledged messages.
- Independent durable consumers each receive the same event.
- Malformed messages reach the sanitized dead-letter stream after maximum delivery.
- Capacity notifications are not captured by `HARBOR_EVENTS`.

### PostgreSQL integration tests

- Lifecycle state and outbox row commit atomically.
- An API crash after commit leaves a publishable outbox row.
- Two publishers cannot publish one outbox row under different event IDs.
- Reprocessing an event ID creates one raw row and one projection effect.
- Out-of-order events preserve correct summaries and additive counts.
- Domain normalization deduplicates equivalent hosts.
- Retention windows operate independently in bounded transactions.

### Metrics and end-to-end tests

- Fleet JSON and gauges come from the same snapshot.
- All providers appear even at zero.
- No metric exposes high-cardinality or sensitive values.
- Existing examples create lifecycle, attempt, command, and supported navigation
  evidence for every provider.
- Fixtures contain no raw cookie, authorization, query-string, HTML, evaluated-source,
  or provider-error value.
- Restarting maintenance eventually records retained events exactly once.
- PostgreSQL and NATS interruption follow the failure behavior above.

## Implementation sequence

1. Add event contracts, registry, JSON serialization, and filtering tests.
2. Add the idempotent `HARBOR_EVENTS` stream manager and acknowledged publisher.
3. Extend `session_events` into the common event/outbox schema through Alembic.
4. Publish lifecycle events immediately after commit and add the outbox recovery loop.
5. Add attempt identity and attempt events around provider acquisition.
6. Add CDP command correlation and terminal command outcomes.
7. Add main-document URL/header normalization for native CDP providers.
8. Add equivalent Camoufox observations only where native evidence exists.
9. Implement the durable recorder, idempotent Postgres writes, and projections.
10. Add bounded JetStream/Postgres retention and maintenance metrics.
11. Add the PostgreSQL fleet snapshot service, `/metrics`, and
    `/v1/fleet/providers`.
12. Add the internal live and historical DEBUG timeline views.
13. Run the provider, persistence, NATS, privacy, and metrics E2E matrices.
14. Update architecture and DEBUG documentation to describe verified behavior only.

## Definition of done

- PostgreSQL remains the sole authority for admission, leases, queues, and capacity.
- Lifecycle state and its durable event row commit atomically.
- Every normalized event uses one versioned contract and stable event ID.
- One NATS publication serves live subscribers and durable JetStream consumers.
- Every provider attempt and valid downstream command has a durable terminal outcome.
- The initial main-document evidence subset is verified independently per provider.
- No raw protocol, body, HTML, cookie, credential, sensitive header, query string, or
  evaluated value enters NATS or PostgreSQL.
- JetStream redelivery and multiple workers cannot duplicate projections.
- JetStream and PostgreSQL detail have explicit tested retention bounds.
- Recorder lag, pending work, failures, dead letters, and retention are observable.
- Prometheus labels are bounded and contain no session/domain data.
- Fleet JSON and Prometheus gauges share one PostgreSQL query service.
- Internal DEBUG is a filtered factual view with no recommendations.
- Existing gateway and provider conformance tests remain green.
