# Roadmap

Harbor develops through narrow vertical slices. Each milestone must preserve the
provider-neutral CDP endpoint and prove its behavior with an unchanged downstream
client.

## Implemented

- [Dependable browser gateway](roadmap/gateway.md): logical sessions, provider
  attempts, transactional admission, leases, queues, and safe WebSocket cleanup.
- [Evidence and observability foundation](roadmap/observability.md): normalized DEBUG
  events, JetStream delivery, PostgreSQL history, and factual domain projections.
- [Managed fleets](roadmap/managed-fleets.md): runtime-neutral reconciliation,
  Chromium and Lightpanda instance slots, Docker Compose runtime support, scaling
  metrics, packing, scale-up, and idle scale-down.
- [No-browser promotion](roadmap/no-browser-promotion.md): let an automatic session
  serve navigation and content through plain HTTP, then acquire Chromium and continue
  the same CDP session after replaying its acknowledged history when another operation
  is observed.
- [Deterministic domain routing](roadmap/deterministic-routing.md): use a conservative
  operator default for unknown domains, qualify cheaper providers with bounded
  background probes, and route from factual cost and compatibility history.

## Next

- Expand the tested portable CDP surface across providers.

## Later

- Add operator-controlled browser, proxy, identity, and network settings.
- Implement the fleet runtime contract for Kubernetes/k3s; the provider-neutral
  reconciler and durable desired state remain unchanged.
- Build the fleet monitoring and session debugging web UI.

Later items are direction, not implementation commitments. A new roadmap item should
define its smallest useful contract and exit condition before development begins.
