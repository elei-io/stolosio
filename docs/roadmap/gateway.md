# Dependable Browser Gateway

Status: implemented

## Public contract

Harbor exposes one provider-neutral CDP WebSocket:

```text
WS /v1/connect
```

The provider is automatic unless the caller explicitly supplies
`harbor.provider.slug`. Ordinary CDP consumers are not required to understand Harbor's
capacity, queues, deadlines, or rejection reasons.

## Two-level admission

Harbor separates logical downstream sessions from acquisition attempts.

```text
connection
    |
    v
global Harbor admission
    |
    v
planner
    |
    v
provider attempt admission
    |
    v
provider connection
```

Global admission limits all live Harbor sessions. A session keeps its global slot from
admission until the downstream connection terminates.

Provider admission independently limits active and queued attempts for Chromium,
Browserless, Lightpanda, and Camoufox. A full provider queue fails only that attempt;
the gateway then terminates and releases the logical session cleanly.

This separation allows a future session to perform an HTTP attempt and later acquire a
browser without changing identity or moving the session itself between queues.

## Lifecycles

Logical session:

```text
requested -> admitted -> open -> closing -> closed
     |          |         |
     +----------+---------+-> failed
```

Acquisition attempt:

```text
requested -> queued -> acquiring -> active -> completed
     |          |          |          |
     +----------+----------+----------+-> failed
```

Sessions own the downstream socket, lease, requested settings, and cleanup. Attempts
own the resolved provider settings, FIFO position, provider resource, and outcome.

## Coordination and failure handling

- PostgreSQL transactions own global admission, provider admission, FIFO ordering,
  leases, and state transitions.
- NATS capacity messages only wake provider waiters; PostgreSQL polling remains the
  correctness fallback.
- The API replica that receives a WebSocket retains ownership of it. Live sockets are
  never placed on JetStream or transferred between workers.
- Downstream disconnect is watched while provider admission is pending.
- One gateway component owns accept, denial, and close behavior.
- Overload before upgrade is an ordinary HTTP service-unavailable response.
- Release is bounded and idempotent; expired leases recover capacity after replica
  failure.
- Lifecycle and attempt events are inserted transactionally into the PostgreSQL outbox.
  The maintenance worker publishes them; NATS cannot delay admission.

## Current limits

- Automatic planning still selects Chromium conservatively.
- A session currently performs one browser attempt.
- HTTP/no-browser attempts and promotion are not implemented yet.
- The registered CDP discovery and target-management HTTP routes remain explicit
  placeholders.
