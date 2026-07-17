# Fleet Management

Harbor owns the lifecycle and capacity of the browser fleets it uses. Infrastructure
platforms provide compute primitives; Harbor decides how many browser workers should
exist, observes which workers are usable, assigns sessions to them, and drains them
before removal.

Prometheus metrics remain operational and analytical signals. They are not the only
mechanism by which browser capacity is managed.

## Terminology

- A **provider** is a browser implementation such as Chromium, Browserless,
  Lightpanda, or Camoufox.
- A **fleet** is a group of compatible browser workers for one provider and one
  process-level configuration.
- An **instance** is one managed browser worker, normally a container or Pod containing
  a browser process.
- A **slot** is capacity for one concurrent Harbor acquisition attempt on an instance.
- A **session** is one downstream CDP connection. A session can create one or more
  owned browser contexts and targets while it occupies a slot.

An instance may expose several slots. Harbor scales instances, while sessions consume
slots within them:

```text
fleet
  ├── instance A: 4 slots, 4 occupied
  └── instance B: 4 slots, 1 occupied

total capacity:     8
occupied capacity:  5
available capacity: 3
```

Slot capacity is provider-specific. Managed Chromium defaults to four sessions per
instance and Browserless defaults to five, matching its Compose `CONCURRENT` limit.
Lightpanda and Camoufox each contribute exactly one slot per managed instance.

## Ownership boundary

Harbor owns:

- Desired and observed fleet state.
- Instance health, readiness, and draining state.
- Slot capacity and attempt placement.
- Scaling policy and safety limits.
- Graceful removal of browser instances.
- An audit trail and metrics for scaling activity.

The infrastructure platform owns:

- Starting and stopping the requested compute resources.
- Container or process isolation.
- Networking and image distribution.
- Node scheduling and infrastructure failure reporting.

Platform-specific controllers translate Harbor's desired state into Docker,
Kubernetes, ECS, Nomad, or another runtime. The FastAPI process does not receive
infrastructure credentials or execute platform commands.

The controller is split across two independent dimensions:

- A provider definition describes how a runtime instance becomes a usable provider
  endpoint and records provider constraints such as its connection port.
- A runtime driver lists, scales, probes, and removes infrastructure units for a named
  deployment.

The provider-neutral reconciler joins those definitions with PostgreSQL desired state.
Docker Compose is the local runtime driver; Kubernetes and k3s can implement the same
runtime contract without changing demand calculation, instance state, placement, or
the public CDP endpoint.

## Fleet configuration

A Harbor administrator controls fleet policy. Initial configuration includes:

```text
provider
minimum_instances
maximum_instances
session_capacity_per_instance
scale_down_cooldown_seconds
```

Configuration is durable in PostgreSQL. Environment values may bootstrap the first
configuration but are not the ongoing source of truth. Administrative changes record
the previous value, new value, timestamp, actor, and configuration version.

Administrative limits are not downstream session settings. A `harbor.*` connection
query may request session behavior, but it cannot raise fleet limits, alter scaling
policy, or bypass capacity safety.

A later web UI uses the same administrative contract as other control-plane clients.
It should show configured limits, desired and observed instances, slot usage, queue
demand, health, and recent reconciliation results.

## Fleet and instance state

Harbor stores both desired fleet state and observed instance state in PostgreSQL.
Instances follow this lifecycle:

```text
starting -> ready -> draining -> stopped
    |         |
    +---------+-> unhealthy
```

Only healthy, ready, non-draining instances contribute admission capacity. Observations
have a lease or freshness deadline; stale instances stop contributing capacity.

Fleet capacity is calculated from observed instances:

```text
total capacity = sum(slots on ready instances)
available capacity = total capacity - assigned slots
```

Desired instance count is not treated as available capacity. A requested instance may
still be starting, unhealthy, or terminating.

## Placement and isolation

Provider attempts remain in provider-level FIFO queues. When a slot becomes available,
Harbor atomically assigns the attempt to a ready instance and records that instance on
the attempt before opening its provider connection.

Placement prefers an instance with available slots and must never exceed its recorded
capacity. A fleet may later use a more specific packing strategy, but placement remains
independent from the public CDP contract.

Each Harbor session normally receives its own browser context. Sharing a browser
process does not remove the need for session ownership: Harbor must identify and clean
up every context and target created by a session without affecting neighboring
sessions. Instance capacity may be raised only after isolation and cleanup have been
tested at that concurrency.

Sessions may share an instance only when their process-level requirements are
compatible. Launch flags, extensions, browser identity, and process-level proxy
configuration may eventually require separate fleets for the same provider.

## Scaling policy

The first policy is deliberately deterministic:

```text
demand = active attempts + queued attempts
required instances = ceil(demand / slots per instance)
desired instances = clamp(required instances, minimum instances, maximum instances)
```

Scale-up responds promptly to demand. Scale-down requires sustained spare capacity and
a cooldown. Policy becomes smarter over time, but it always stays inside administrator
limits and never trades correctness for lower cost.

Harbor must not maintain two competing owners of replica count. A fleet managed by
Harbor cannot simultaneously be controlled by Kubernetes HPA or another autoscaler.

## Draining and scale-down

Lowering a limit must not abruptly terminate active sessions:

- Lowering maximum instances marks excess instances for draining.
- Lowering slots per instance stops new assignments above the new limit while existing
  sessions finish.
- A draining instance receives no new attempts.
- An instance is removed after its assignments reach zero or a bounded administrative
  drain deadline is reached.
- Forced removal fails affected sessions truthfully; it never reports successful
  acquisition.

The initial implementation may scale down only when the entire fleet is idle. Selective
instance draining is added only when the platform driver can remove the intended
instance safely.

## Controller boundary

Fleet control runs separately from the API:

```text
Harbor API and admission
          |
          v
PostgreSQL desired and observed state
          ^
          |
Harbor fleet controller
          |
          v
Docker / Kubernetes / another platform
```

Reconciliation is idempotent. A controller observes desired state, observes the
platform, applies the smallest required change, updates instance state, and repeats.
Only one active controller may mutate a particular fleet.

Controller failure stops scaling. Existing assigned sessions continue, while stale
observations eventually stop contributing capacity for new attempts. PostgreSQL failure
stops reconciliation and admission. Instance loss immediately removes its capacity and
causes its assigned attempts to fail or disconnect normally.

## Observability

Fleet state and actions are exposed through bounded Prometheus metrics and the future
administrative UI. DEBUG remains a factual stream about an individual session and does
not contain scaling recommendations or controller decisions.

Fleet observability includes:

- Desired, observed, ready, unhealthy, and draining instance counts.
- Total, occupied, and available slots.
- Queue depth and oldest queued attempt age.
- Reconciliation success, failure, and latency.
- Scaling actions by provider, direction, and stable outcome.
- Time of the last successful reconciliation.

The Docker implementation for all four managed browser providers is specified in
[Managed Fleets](roadmap/managed-fleets.md).
