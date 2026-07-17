# Managed Fleets

Status: implemented

This milestone proves that Harbor can pack sessions onto browser instances, measure
unmet demand, and reconcile all managed browser fleets through Docker
Compose. The reconciliation core is independent of Docker so another runtime can use
the same desired state and scaling policy.

The durable design is defined in [Fleet Management](../FLEET_MANAGEMENT.md).

## Scope

The implemented vertical slice covers:

- Plain Chromium and Browserless with configurable multi-session slots.
- Lightpanda and Camoufox with one session slot per instance.
- Docker Compose as the infrastructure runtime.
- A Harbor fleet controller running as a host process.
- Configurable minimum and maximum instances.
- Configurable session slots per instance.
- Provider-attempt placement on a specific instance.
- Immediate scale-up and cooldown-based fleet-idle scale-down.
- Administrative configuration stored in PostgreSQL.
- Fleet state, controller metrics, and an end-to-end scaling test.

The milestone does not implement a Kubernetes runtime driver, selective draining of a
busy fleet, predictive scaling, cost optimization, multiple controller replicas, or
fleet classes for different process-level browser settings.

## Target flow

```text
CDP connection
      |
      v
global session admission
      |
      v
provider queue -----------------> scaling policy
      |                                |
      |                                v
      |                         desired instances
      |                                |
      |                                v
      |                     host Docker controller
      |                                |
      |                                v
      |                     Compose browser workers
      |                                |
      +<----- ready instance inventory-+
      |
      v
instance slot assignment -> direct CDP connection
```

## Persistent state

Add a `provider_fleets` table containing:

```text
provider
minimum_instances
maximum_instances
session_capacity_per_instance
scale_down_cooldown_seconds
desired_instances
configuration_version
last_scale_up_at
last_scale_down_at
idle_since
last_reconciled_at
controller_status
```

Add a `provider_instances` table containing:

```text
instance_id
provider
platform
endpoint
state
capacity
observed_at
observation_expires_at
started_at
ready_at
draining_at
stopped_at
```

Add a nullable `provider_instance_id` reference to each acquisition attempt. Assignment
and slot reservation occur in the same PostgreSQL transaction that claims a queued
attempt.

Add a `fleet_configuration_events` audit table containing the fleet, configuration
version, previous and new values, actor, and timestamp.

Applied migrations are not rewritten. This work adds a new Alembic migration.

## Internal packages

Introduce a bounded fleet subsystem:

```text
backend/fleet/
├── bootstrap.py
├── contracts.py
├── policy.py
├── repository.py
├── reconciler.py
├── service.py
├── providers/
│   ├── contracts.py
│   ├── chromium.py
│   └── lightpanda.py
├── runtimes/
│   └── docker_compose.py
└── controllers/
    └── docker.py
```

- `contracts.py` defines fleet configuration, observed instances, and the runtime
  contract.
- `policy.py` is a pure deterministic demand-to-instance calculation.
- `repository.py` owns transactional fleet state and slot assignment.
- `reconciler.py` applies desired state through any runtime implementation.
- `providers/` contains provider endpoint facts without infrastructure operations.
- `runtimes/` contains platform operations without provider admission policy.
- `service.py` exposes administrative configuration and fleet snapshots.
- `controllers/docker.py` is the thin local process that wires both dimensions
  together and exposes controller metrics.

Provider adapters receive the assigned instance endpoint. They do not discover or
scale infrastructure themselves.

## Administrative configuration

The local administrative contract exposes fleet reads and updates separately from the
downstream CDP API:

```text
GET   /v1/admin/fleets
GET   /v1/admin/fleets/{provider}
PATCH /v1/admin/fleets/{provider}
```

For this milestone the mutation route is development-only until Harbor has an
authentication and authorization boundary. The service layer remains the future web
UI contract.

Validation requires:

- `0 <= minimum_instances <= maximum_instances`.
- `session_capacity_per_instance >= 1` for an enabled fleet.
- Positive reconciliation and cooldown intervals.
- A bounded maximum fleet size.

Updates are versioned and audited. Downstream session query parameters cannot change
these values.

## Docker controller

Run the controller on the Compose host:

```bash
uv run python -m backend.fleet.controllers.docker
```

The controller:

1. Reads current demand and fleet configuration for Chromium, Browserless, Lightpanda,
   and Camoufox.
2. Evaluates the pure scaling policy and persists the desired instance count.
3. Inspects containers using Compose project and service labels.
4. Applies one scale change per reconciliation cycle.
5. Resolves each container's internal network address.
6. Probes each instance's provider port through the runtime driver.
7. Upserts starting, ready, unhealthy, and stopped observations.
8. Expires instances that disappear or stop reporting.
9. Records a stable reconciliation outcome and repeats with backoff.

The controller executes Docker and Compose commands; the FastAPI process does not. It
must be safe to restart and must converge from the currently running containers rather
than assuming its previous command succeeded.

Managed Compose services must not publish a fixed host port for every replica.
Harbor reaches assigned instances through the Compose network. Development tooling may
provide a separate single-instance direct-debug profile if needed.

## Placement and capacity

Provider FIFO remains unchanged. A claim succeeds only when a ready provider instance
has an unreserved slot. The repository locks the relevant fleet and instance rows,
selects a usable instance, records the attempt assignment, and then returns the endpoint
to the adapter.

The first placement rule chooses the ready instance with the most occupied slots that
still has capacity. Packing work leaves whole instances idle and therefore eligible for
future scale-down.

An instance contributes no capacity while starting, unhealthy, draining, stale, or
stopped. Losing an assigned instance fails its active attempts with a stable
provider-loss reason.

Before enabling more than one slot per Chromium instance, tests must prove that:

- Concurrent Harbor sessions receive separate browser contexts.
- One session cannot close or mutate another session's owned contexts and targets.
- Disconnect cleanup removes only the terminating session's resources.
- Abrupt client loss does not leak contexts or slots.
- Browser-process loss fails every affected session and releases every assignment.

## Initial scaling policy

Use the following pure calculation:

```text
demand = active attempts + queued attempts
required = ceil(demand / session capacity per instance)
desired = clamp(required, minimum instances, maximum instances)
```

Policy behavior:

- Scale up as soon as required instances exceeds desired instances.
- Add no more than one instance per reconciliation cycle.
- Include starting instances in desired state so slow startup does not cause repeated
  scale-up.
- Scale down by one only after the fleet has no active or queued attempts for the full
  cooldown.
- Never scale below the configured minimum.
- If the maximum is reached, ordinary provider queue limits and timeouts continue to
  provide backpressure.

Selective scale-down while other instances remain busy is deferred.

## Metrics

Retain the existing gateway and provider-attempt metrics and add:

```text
harbor_provider_desired_instances{provider}
harbor_provider_observed_instances{provider}
harbor_provider_ready_instances{provider}
harbor_provider_draining_instances{provider}
harbor_provider_unhealthy_instances{provider}
harbor_provider_total_slots{provider}
harbor_provider_available_slots{provider}
harbor_provider_scaling_actions_total{provider,direction,outcome}
harbor_provider_reconcile_errors_total{provider,reason}
harbor_provider_last_successful_reconcile_timestamp{provider}
```

Labels remain bounded. Instance IDs, endpoints, session IDs, and arbitrary error text
do not become Prometheus labels. Scaling facts belong in controller logs and metrics,
not the per-session DEBUG stream.

## Failure behavior

- Controller unavailable: existing assigned sessions continue; scaling pauses and stale
  observations eventually stop admitting new attempts.
- PostgreSQL unavailable: admission and reconciliation fail closed.
- Scale command failure: record the stable failure and retry with bounded backoff.
- Instance startup timeout: replace an instance only after it has remained unhealthy
  for the configured timeout, through normal reconciliation.
- Instance disappearance: remove its capacity and fail assigned attempts truthfully.
- Configuration lowered below current use: existing sessions finish; new placement
  respects the new limit.
- Process restart: reconstruct actual state from PostgreSQL and Docker inspection.

## Acceptance test

The Docker E2E tests use one Chromium instance with two slots. Browserless,
Lightpanda, and Camoufox are each constrained to one Harbor slot per instance during
the scaling test; Browserless still retains its process-level concurrency ceiling:

1. Connect the first session and verify the fleet remains at one instance.
2. Connect the second concurrent session and verify it shares that instance in an
   isolated context.
3. Connect a third session and verify it queues.
4. Verify desired replicas becomes two.
5. Verify the controller starts and discovers a second healthy Chromium instance.
6. Verify the queued third session acquires a slot on the second instance.
7. Close all sessions and verify contexts, attempts, and slots are released.
8. Wait for the configured cooldown and verify the fleet returns to one instance.
9. Verify no queue leaks, capacity leaks, failed acquisitions, or WebSocket lifecycle
   errors.
10. Hold one Lightpanda session, queue a second, scale to two Lightpanda instances, and
    verify both the acquired session and scale-down to one instance.
11. Repeat the single-slot scale-up and scale-down proof for Browserless and Camoufox.

## Exit condition

The milestone is complete when Harbor can pack concurrent isolated sessions into a
compatible multi-slot instance, scale every Compose browser fleet from measured
demand, use newly observed capacity, and scale back to configured minimums without
disrupting a session or requiring downstream knowledge of either the provider or
runtime.
