# Architecture

Harbor presents one downstream endpoint:

```text
WS /v1/connect
```

Existing CDP and Playwright `connect_over_cdp()` clients should need only a URL change.
Harbor settings use the `harbor.*` query namespace; provider addresses and credentials
are never public API.

## Data path

```text
downstream CDP
      |
 FastAPI WebSocket
      |
 admission + planner
   /       |        \
 HTTP  Browserless  Browserbase
 facade   worker CDP  session CDP
```

The HTTP facade implements only its explicitly tested contract. Browserless and
Browserbase are opaque, bidirectional CDP transports. Harbor validates the JSON
envelope needed for correlation and observation, but it does not decide whether an
individual browser method is supported.

An automatic session may make one HTTP-to-browser escalation. It cannot move from one
browser provider to another. One Harbor browser attempt owns one upstream browser
session for its lifetime.

## Promotion and escalation

Promotion and escalation are separate mechanisms:

- **Promotion** is background policy work. Probes compare HTTP and Browserless results,
  then update durable domain evidence used by future plans. Promotion never changes a
  live session.
- **Escalation** is a live correctness path. An automatic HTTP session immediately
  acquires a browser when HTTP cannot execute a CDP method, its request fails, or its
  status, headers, response size, or content sanity check fails.

Browserbase is never probed automatically and never contributes promotion evidence.
Operators may run an explicit paid diagnostic probe. When enabled,
Harbor assumes it works and keeps it as the terminal escalation candidate. Failure
there is terminal because Harbor has no more capable provider to try.

## Capacity

PostgreSQL transactionally owns logical-session admission, provider queues, leases,
Browserless slot assignments, and Browserbase external quota.

Browserless workers form a managed fleet. A worker exposes multiple independent
session slots, and a separate fleet controller reconciles desired workers against
Docker or another compute platform. Only ready, healthy, non-draining workers
contribute slots.

The Kubernetes/k3s runtime uses a Harbor-owned StatefulSet so ordinal scale-down can
be drained safely. Helm and GitOps own a static workload template rather than the live
replica count. See [Kubernetes and k3s](KUBERNETES.md).

Browserbase is external capacity. Harbor applies an administrator-configured active
session and queue limit before calling the Browserbase Sessions API. This limit can
track the subscription ceiling or enforce a stricter cost budget.

## Lifecycle and observations

Attempt lifecycle is durable and cleanup is idempotent. Provider session IDs and
provider start/end timestamps are stored without persisting provider connection URLs.
Harbor derives:

- capacity-occupied time;
- browser-connected time;
- provider-reported browser time; and
- estimated billable time.

CDP commands receive timestamps at receipt, upstream forwarding, and terminal
response. Prometheus observes bounded command-domain latency. Generic successful
commands are accumulated into one bounded per-attempt method summary; only failures
and interruptions produce individual command events.

PostgreSQL folds attempt summaries into a cumulative provider-and-method cost
aggregate, with browser-connected time capped at the attempt total and residual time
assigned to session overhead. It does not retain individual command executions.
DEBUG events contain normalized, redacted observations rather than arbitrary
parameters, page data, credentials, or diagnoses.

## Process boundaries

- `backend/api/` owns HTTP/WebSocket transport and application lifecycle.
- `backend/proxy/` owns planning, settings, admission, adapters, and protocol transport.
- PostgreSQL is the durable source of truth.
- NATS Core handles live coordination; JetStream handles durable observation delivery.
- Fleet controllers reconcile infrastructure separately from FastAPI.
