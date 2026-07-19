# Fleet and Capacity Management

Harbor manages Browserless as a horizontal fleet. Browserbase is externally hosted, so
Harbor manages an admission quota rather than infrastructure. Direct HTTP work also
uses an admission quota because it consumes Harbor process and network capacity
without browser instances.

## Browserless

A Browserless instance is one worker process. Each instance exposes a configurable
number of independent session slots:

```text
usable capacity = ready, healthy, non-draining instances × slots per instance
desired instances = clamp(ceil(demand / slots per instance), minimum, maximum)
```

Placement atomically assigns an acquisition attempt to a free slot. Sessions on the
same worker remain separate upstream browser sessions. The fleet controller runs
outside FastAPI and reconciles Docker or another compute platform from PostgreSQL.
Browserless itself is not assumed to scale Harbor's worker fleet.

Administrators control minimum and maximum instances, session slots per instance,
maximum queued attempts, scale-down cooldown, and whether the fleet is enabled. Only
current, ready, healthy, non-draining observations count as capacity.

The session-slots value has no Harbor-imposed upper bound. Five is only the initial
database default. Startup inserts this default only when no Browserless fleet row
exists and never overwrites a saved value. The fleet controller applies the stored
session capacity to Browserless workers and reports observed concurrency back to
PostgreSQL. A changed value is applied once the fleet has no live demand so existing
browser sessions are not interrupted; admission continues to use observed worker
capacity until reconfiguration completes. During a mixed-capacity transition, fleet
policy adds instances when live demand exceeds the capacity actually provisioned.
An instance already requested but not yet observed is treated as an in-flight scaling
operation so a slow image pull cannot cause runaway expansion.

The fleet controller exposes bounded Prometheus telemetry for scaling actions,
request-to-ready latency, request-to-first-assignment latency, and instances removed
without ever serving an acquisition attempt. These observations are recorded from the
controller's successful scale request, so they measure Harbor's operational path
rather than approximating it from pod creation timestamps.

On Kubernetes and k3s, Harbor owns a Browserless StatefulSet generated from a
GitOps-managed workload template. Harbor writes its replica count directly; KEDA and
HPA must not target that StatefulSet. Deterministic StatefulSet ordinals let Harbor
drain the instance Kubernetes will remove before lowering replicas. See
[Kubernetes and k3s](KUBERNETES.md).

## Browserbase

Browserbase supplies its own infrastructure. Harbor stores a durable external-provider
limit with enabled state, maximum active sessions, maximum queued attempts, and an
audited configuration version.

Admission checks and consumes this capacity transactionally. The limit can match a
Browserbase subscription or sit below it as a cost ceiling. Credentials and project ID
remain deployment secrets. Startup inserts a disabled product default only when no
Browserbase limit row exists and never derives enablement or limits from environment
variables. An operator cannot enable Browserbase without an API key; the
administrative API returns a clear conflict instead of accepting an unusable provider
configuration.

## Direct HTTP

Harbor stores durable maximum-active and maximum-queued limits for direct HTTP work.
Administrators configure both values, and can enable or disable HTTP admission, from
the Fleets page. Admission applies changes to new acquisition attempts immediately;
active requests are allowed to finish.

## Administrative boundary

Fleet and external quota limits are administrative policy, not `harbor.*` session
settings. Explicit downstream provider selection cannot bypass them.

PostgreSQL is authoritative for queues, leases, slot assignments, desired capacity,
external quotas, routing policy, and configuration audit. API startup may create
missing policy rows with product defaults, but a restart or redeploy never overwrites
operator-saved values. Environment variables are reserved for credentials, endpoints,
database and messaging connections, and process/runtime mechanics. Prometheus exposes
bounded operational aggregates; session IDs, URLs, domains, and provider error text
remain in durable observations instead of metric labels.
