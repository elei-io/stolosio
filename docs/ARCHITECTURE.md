# Architecture

Stolosio presents two downstream endpoints:

```text
WS   /v1/connect
POST /v1/capture
```

Existing CDP and Playwright `connect_over_cdp()` clients should need only a URL change.
Stolosio settings use the `stolosio.*` query namespace; provider addresses and credentials
are never public API. `POST /v1/capture` returns one page and chooses its own method
(see [Page capture](CAPTURE.md)).

## Acquisition clients

A `/v1/connect` session connects directly to the provider named by
`stolosio.provider.slug`. Without it, the session uses `browserless`, the local
Stolosio-managed fleet. The only other provider is paid `browserless_cloud`, used only
when named and when an operator has enabled its capacity. Stolosio acquires the browser
before accepting the WebSocket, and the session never moves to another provider.

`stolosio.session.admission_timeout_ms` (1–60,000) bounds the combined network-policy
lookup, logical-session admission, provider queue/acquisition and WebSocket acceptance.
It cannot extend server-side provider limits. It ends at acceptance, so it is not a
navigation or session-lifetime timeout. Clients must separately bound CDP bootstrap,
navigation and cleanup; use a slightly longer client connection budget to receive a
denial. Cancellation drains pending admissions and releases late-acquired capacity.

Capacity-full, provider-queue timeout and session-admission timeout denials use HTTP
429 with `Retry-After: 5` before WebSocket acceptance. Provider availability failures
remain 503/504. After acceptance, existing CDP/WebSocket errors apply. Clients should
jitter capacity retries and avoid counting pre-accept denials as page captures.

Periplus acquires pages through `POST /v1/capture` rather than CDP.

## Data path

```text
downstream CDP
      |
 FastAPI WebSocket
      |
     admission
    /         \
 Browserless   Browserless cloud
 worker CDP    session CDP
```

Both providers are opaque, bidirectional CDP transports. Stolosio validates the JSON
envelope needed for correlation and observation, but it does not decide whether an
individual browser method is supported. One Stolosio attempt owns one upstream browser
session for its lifetime.

Page capture uses the same global session admission and acquires provider capacity only
when a browser tier is needed. Verified HTTP captures require no browser slot. It fetches plain HTTP through the
egress proxy and renders on a local Browserless slot when HTTP is not enough; the per-URL
method cache in PostgreSQL (`capture_method_cache`) records where plain HTTP was
confirmed sufficient. With `resolve_bot_challenges` enabled, capture tries the internal
resolver before an available external resolver. PostgreSQL `capture_results` stores
one compact acquisition fact per completed capture, in the same transaction as its
outbox event. This projection survives DEBUG retention and powers the admin capture
analytics; downstream products own their own customer usage and usefulness assessments.

## Capacity

PostgreSQL transactionally owns logical-session admission, provider queues, leases,
Browserless slot assignments, and Browserless cloud external quota.

Browserless workers form a managed fleet. A worker exposes multiple independent
session slots, and a separate fleet controller reconciles desired workers against
Docker or another compute platform. Only ready, healthy, non-draining workers
contribute slots.

The Kubernetes/k3s runtime uses a Stolosio-owned StatefulSet so ordinal scale-down can
be drained safely. Helm and GitOps own a static workload template rather than the live
replica count. See [Kubernetes and k3s](KUBERNETES.md).

Browserless cloud is external capacity. Stolosio applies an administrator-configured
active session and queue limit before connecting to it, and uses it only when a session
names it or a capture resolves a bot challenge. This limit can track the subscription
ceiling or enforce a stricter cost budget.

## Lifecycle and observations

Attempt lifecycle is durable and cleanup is idempotent. Provider session IDs and
provider start/end timestamps are stored without persisting provider connection URLs.
Stolosio derives:

- capacity-occupied time;
- browser-connected time;
- provider-reported browser time; and
- chargeable time.

Each attempt is charged its capacity-occupied time, from acquisition to release, times
its provider's operator-managed rate (`provider_cost_rates`, cost units per second).

CDP commands receive timestamps at receipt, upstream forwarding, and terminal
response. Prometheus observes bounded command-domain latency. Generic successful
commands are accumulated into one bounded per-attempt method summary; only failures
and interruptions produce individual command events.

PostgreSQL folds attempt summaries into a cumulative provider-and-method cost
aggregate, with browser-connected time capped at the attempt total and residual time
assigned to an unattributed-session row. Attempts separately retain a bounded shadow
summary of command-active union time and fixed internal lifecycle phases. Stolosio does
not retain individual successful command executions.
DEBUG events contain normalized, redacted observations rather than arbitrary
parameters, page data, credentials, or diagnoses.

## Network policy

PostgreSQL stores Stolosio's operator-managed global domain blocklist. Each provider
attempt snapshots the current policy version. Browser attempts apply the list to
newly attached network-capable CDP targets before exposing them downstream; capture's
plain HTTP fetch applies it to every redirect hop. Clients cannot override administrative
network policy through `stolosio.*` query parameters. See
[Network policy](NETWORK_POLICY.md).

## Process boundaries

- `backend/api/` owns HTTP/WebSocket transport and application lifecycle.
- `backend/proxy/` owns settings, admission, adapters, capture hosting, and protocol
  transport.
- PostgreSQL is the durable source of truth.
- NATS Core handles live coordination; JetStream handles durable observation delivery.
- Fleet controllers reconcile infrastructure separately from FastAPI.
