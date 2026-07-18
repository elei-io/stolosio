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
- [Provider transitions](roadmap/provider-transitions.md): let an automatic session
  serve navigation and content through plain HTTP, then acquire Chromium and continue
  the same CDP session after replaying safe acknowledged history when another operation
  is observed.
- [Domain eligibility routing](roadmap/deterministic-routing.md): probe acquisition
  health independently, suppress exact runtime incompatibilities, restore after one
  later compatible session, and order eligible providers by cost.

## Next

- Expand the tested portable CDP surface across providers.

## Later

- Add operator-controlled browser, proxy, identity, and network settings.
- Implement the fleet runtime contract for Kubernetes/k3s; the provider-neutral
  reconciler and durable desired state remain unchanged.
- Build the fleet monitoring and session debugging web UI.

Later items are direction, not implementation commitments. A new roadmap item should
define its smallest useful contract and exit condition before development begins.
