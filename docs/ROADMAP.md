# Roadmap

Stolosio develops through narrow vertical slices. Each milestone must preserve the
provider-neutral CDP endpoint and prove its behavior with an unchanged downstream
client.

## Implemented

- [Dependable browser gateway](roadmap/gateway.md): logical sessions, provider
  attempts, transactional admission, leases, queues, and safe WebSocket cleanup.
- [Evidence and observability foundation](roadmap/observability.md): normalized DEBUG
  events, JetStream delivery, PostgreSQL history, and factual domain projections.
- [Managed Browserless fleet](FLEET_MANAGEMENT.md): runtime-neutral reconciliation,
  multi-session instances, Docker Compose runtime support, packing, scale-up, and idle
  scale-down.
- [Page capture](CAPTURE.md): return a page's content through plain HTTP when it
  proves enough, a render on the managed fleet otherwise, and optional paid challenge
  resolution on Browserless cloud.

- [Kubernetes/k3s runtime](KUBERNETES.md): StatefulSet reconciliation and draining.
- Operator UI: fleet policy, session history, live events, and capture/cost analytics.

## Next

- Expand the tested portable CDP surface across providers.

## Later

- Add operator-controlled browser, proxy, identity, and network settings.

Later items are direction, not implementation commitments. A new roadmap item should
define its smallest useful contract and exit condition before development begins.
