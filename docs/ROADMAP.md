# Roadmap

Harbor develops through narrow vertical slices. Each milestone must preserve the
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
- [Bounded HTTP execution](NO_BROWSER.md): serve navigation and content without a
  browser, then escalate once to a native CDP provider when another operation is
  observed.
- [Domain routing](ARCHITECTURE.md): use current health evidence and cost to order
  Browserless and retain Browserbase as the capacity-controlled terminal fallback.

## Next

- Expand the tested portable CDP surface across providers.

## Later

- Add operator-controlled browser, proxy, identity, and network settings.
- Implement the fleet runtime contract for Kubernetes/k3s; the provider-neutral
  reconciler and durable desired state remain unchanged.
- Build the fleet monitoring and session debugging web UI.

Later items are direction, not implementation commitments. A new roadmap item should
define its smallest useful contract and exit condition before development begins.
