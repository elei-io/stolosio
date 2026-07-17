# Evidence and Observability Foundation

Status: implemented

Harbor records filtered facts about sessions, acquisition attempts, CDP commands, and
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
harbor.v1.events.session.<session_id>
```

`event_id` is stable across retries and is also used as the JetStream message ID.
PostgreSQL recording is idempotent by event ID. Harbor preserves observation order for
one live connection but does not invent a total order across processes.

Registered event families are:

- Session lifecycle: requested, admitted, open, closing, closed, and failed.
- Acquisition attempts: started, queued, acquiring, connected, closed, and failed.
- CDP commands: received, succeeded, failed, and interrupted.
- Browser observations: navigation, main-document response, page lifecycle, page crash,
  and provider disconnect.

Adding an event requires a typed payload and normalization tests. Arbitrary event names
or unvalidated payload fields are rejected.

## Durability

Session and attempt lifecycle events are written in the same PostgreSQL transaction as
their state change. The maintenance worker forwards unpublished rows to JetStream; NATS
availability therefore cannot delay or invalidate admission.

Protocol and browser observations are published to JetStream without blocking CDP
transport. The maintenance recorder consumes them durably and writes them to PostgreSQL
idempotently. Failed deliveries are bounded and use sanitized dead-letter records.

Retention is bounded independently for JetStream event detail, PostgreSQL event rows,
terminal sessions, and domain projections.

## Evidence and projections

The recorder maintains factual projections for:

- Seen domains.
- Domains observed during each session.
- Seen CDP methods.
- CDP method counts per domain and session.

These projections contain no confidence scores or recommendations. Their future use is
described in [ANALYTICS.md](../ANALYTICS.md) and no-browser policy is described in
[NO_BROWSER.md](../NO_BROWSER.md).

## Privacy boundary

Filtering happens before publication. By default Harbor does not put the following into
NATS, DEBUG, metrics, or analytical projections:

- HTML, response bodies, or evaluated values.
- Cookies, authorization headers, credentials, or proxy secrets.
- Raw query values or fragments.
- Raw CDP, Playwright, or Juggler messages.
- Arbitrary provider exception text.

URLs are normalized and the header allowlist is intentionally small. Prometheus labels
are bounded and never contain domains, URLs, session IDs, or arbitrary error strings.

## Operational views

Harbor exposes current PostgreSQL-backed fleet state through:

```text
GET /v1/fleet/gateway
GET /v1/fleet/providers
GET /metrics
```

Gateway metrics describe logical session intake. Provider metrics describe acquisition
attempts and provider capacity:

```text
harbor_gateway_active_sessions
harbor_gateway_capacity
harbor_provider_active_attempts
harbor_provider_queued_attempts
harbor_provider_capacity
harbor_provider_oldest_queued_attempt_seconds
```

Process-local counters and histograms cover acquisitions, session outcomes, commands,
provider failures, event delivery, recorder lag, dead letters, and retention work.

The operator UI loads retained normalized events and follows the cross-session live
tail through:

```text
GET /v1/admin/events
GET /v1/admin/events/stream
```

The live endpoint uses SSE because activity delivery is one-way. JetStream stream
sequences provide resumable cursors while PostgreSQL remains the historical source.

## DEBUG delivery

The initial downstream DEBUG contract is:

```text
WS /v1/debug?harbor.session.reference=<uuid>
```

The stream resolves the caller-provided reference to Harbor's internal session, replays
retained PostgreSQL history, removes duplicate event IDs, and follows the JetStream live
tail until the session terminates. Buffers are bounded; a slow consumer is disconnected
instead of slowing browser traffic.

See [DEBUG.md](../DEBUG.md) for the observation and redaction contract.

## Current limits

- Observation coverage is intentionally narrow and focuses on the main document.
- The downstream DEBUG endpoint supports one session reference per connection; the
  administrative activity feed separately supports a filtered cross-session tail.
- Public authentication, authorization scopes, and resumable cursors are not implemented.
- Metrics are scaling and operational signals, not domain analytics.
- The planner uses bounded factual projections; DEBUG events remain observations rather
  than routing recommendations.
