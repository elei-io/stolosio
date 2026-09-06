# Evidence and Observability Foundation

Status: implemented

Stolosio records filtered facts about sessions, acquisition attempts, CDP commands, and
browser activity. These facts support the DEBUG stream, operational metrics, durable
history, and future analytics without placing observation delivery on the admission
path.

This layer records what happened. It does not diagnose a session, recommend a provider,
or make routing decisions.

## System shape

```text
gateway and adapters
        |
        +--> transactional lifecycle outbox in PostgreSQL
        |
        +--> normalized session events
                       |
                       v
             NATS + JetStream
                |          |
                v          v
          DEBUG stream   maintenance recorder
                              |
                              v
                 PostgreSQL evidence and projections
```

PostgreSQL remains the authority for admission, leases, queues, and capacity. NATS
capacity notifications are ephemeral wakeups and are separate from the observation
stream.

## Event contract

Every normalized event uses the same versioned envelope:

```text
event_id
schema_version
event_type
session_id
occurred_at
provider       optional
attempt_id     optional
payload
```

Events are published to:

```text
stolosio.v1.events.session.<session_id>
```

`event_id` is stable across retries and is also used as the JetStream message ID.
PostgreSQL recording is idempotent by event ID. Stolosio preserves observation order for
one live connection but does not invent a total order across processes.

Registered event families are:

- Session lifecycle: open, closed, and failed.
- Acquisition attempts: connected, closed, and failed.
- CDP commands: one bounded per-attempt summary, plus individual failures and
  interruptions.
- Browser observations: main-document navigation/response/failure, redirects, content
  size, page crash, console/JavaScript failures, and provider disconnect.

Adding an event requires a typed payload and normalization tests. Arbitrary event names
or unvalidated payload fields are rejected.

## Durability

Session and attempt lifecycle events are written in the same PostgreSQL transaction as
their state change. The maintenance worker forwards unpublished rows to JetStream; NATS
availability therefore cannot delay or invalidate admission.

Protocol and browser observations are published to JetStream without blocking CDP
transport. The maintenance recorder consumes them durably and writes them to PostgreSQL
idempotently. Failed deliveries are bounded and use sanitized dead-letter records.

JetStream and PostgreSQL retain the same compact event contract. There are no
high-volume and low-volume event classes or individual successful-command records.
Operational entities and factual projections retain their own product state. The
transactional attempt command summary is cleared after its bounded aggregate has
been projected.

PostgreSQL is the reconstruction source when NATS is replaced. Stolosio owns and
continuously reconciles its JetStream streams and consumers. Creating a fresh event
stream triggers a bounded replay of PostgreSQL records that still fall inside the
configured JetStream retention window.

## Evidence and projections

The recorder maintains factual projections for:

- Seen domains.
- Domains observed during each session.
- Provider-and-method command counts, failures, interruptions, latency, attributed
  browser time, and attributed cost. Method identity is capped per provider, with
  overflow folded into `__other__`.

These projections contain no confidence scores or recommendations. Their future use is
described in [ANALYTICS.md](../ANALYTICS.md) and no-browser policy is described in
[NO_BROWSER.md](../NO_BROWSER.md).

## Privacy boundary

Filtering happens before publication. By default Stolosio does not put the following into
NATS, DEBUG, metrics, or analytical projections:

- HTML, response bodies, or evaluated values.
- Cookies, authorization headers, credentials, or proxy secrets.
- Raw query values or fragments.
- Raw CDP, Playwright, or Juggler messages.
- Arbitrary provider exception text.

URLs are normalized and the header allowlist is intentionally small. Prometheus labels
are bounded and never contain domains, URLs, session IDs, or arbitrary error strings.

## Operational views

Stolosio exposes current PostgreSQL-backed fleet state through:

```text
GET /v1/fleet/gateway
GET /v1/fleet/providers
GET /metrics
```

Gateway metrics describe logical session intake. Provider metrics describe acquisition
attempts and provider capacity:

```text
stolosio_gateway_active_sessions
stolosio_gateway_capacity
stolosio_provider_active_attempts
stolosio_provider_queued_attempts
stolosio_provider_capacity
stolosio_provider_oldest_queued_attempt_seconds
```

Process-local counters and histograms cover acquisitions, session outcomes, commands,
provider failures, event delivery, recorder lag, dead letters, and retention work.

The operator UI loads retained normalized events and follows the cross-session live
tail through:

```text
GET /v1/admin/events
GET /v1/admin/events/stream
```

It reads cumulative browser-time attribution through:

```text
GET /v1/admin/command-costs
```

The live endpoint uses SSE because activity delivery is one-way. JetStream stream
sequences provide resumable cursors while PostgreSQL remains the historical source.

## DEBUG delivery

The downstream DEBUG contract is:

```text
WS /v1/debug?stolosio.session.reference=<uuid>
```

The stream resolves the caller-provided reference to Stolosio's internal session and uses
an ephemeral ordered JetStream consumer to replay and follow that session until it
terminates. Buffers are bounded; a slow consumer is disconnected instead of slowing
browser traffic.

See [DEBUG.md](../DEBUG.md) for the observation and redaction contract.

## Current limits

- Observation coverage is intentionally narrow and focuses on the main document.
- The downstream DEBUG endpoint supports one session reference per connection; the
  administrative activity feed separately supports a filtered cross-session tail.
- Public authentication, authorization scopes, and resumable cursors are not implemented.
- Metrics are scaling and operational signals, not domain analytics.
- The planner uses bounded factual projections; DEBUG events remain observations rather
  than routing recommendations.
