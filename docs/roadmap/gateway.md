# Dependable Browser Gateway

Status: implemented and verified on 2026-07-16
Roadmap package: 1
Target: the first dependable Harbor session gateway

Verification at completion:

- Repository suite: 49 passed, 13 opt-in E2E tests skipped.
- Compose provider conformance suite: 13 passed across Chromium, Browserless,
  Lightpanda, Camoufox, and the omitted-provider automatic path.
- Fresh production API image build: passed.

## Purpose

Harbor gives downstream clients one browser-neutral CDP connection endpoint. The
client does not select or manage browser infrastructure unless it deliberately
overrides Harbor's choice.

The public value proposition is:

> Connect to Harbor and let Harbor acquire a correct browser session as quickly and
> cheaply as it currently knows how.

This work package establishes the session boundary on which later provider selection,
HTTP-only acquisition, proxy selection, analytics, scaling metrics, and optimization
will be built. The first automatic policy is deliberately conservative and static;
making it intelligent is not part of this package.

## Goals

- Provide one provider-neutral WebSocket endpoint compatible with Playwright
  `chromium.connect_over_cdp()`.
- Accept Harbor settings through collision-resistant `harbor.*` query parameters.
- Treat an omitted setting as `auto` whenever automatic selection is meaningful.
- Allow an explicit provider override without changing the endpoint path.
- Own session admission, queuing, capacity, leases, and terminal cleanup in PostgreSQL so
  multiple Harbor replicas behave as one gateway.
- Pass CDP through to Chromium, Browserless, and Lightpanda only for commands Harbor
  has explicitly verified for that provider.
- Present a bounded CDP facade over Camoufox for the commands required by the three
  downstream examples.
- Return stable, sanitized failures for invalid settings, unavailable providers,
  capacity failures, timeouts, and unsupported commands.
- Prove supported behavior using provider-specific conformance tests.

## Non-goals

- A cost- or evidence-based automatic provider policy.
- Fallback or retry from one provider to another.
- HTTP/no-browser execution.
- Proxy selection or proxy credentials.
- Persistent user or tenant configuration.
- Analytical Postgres projections or JetStream-to-Postgres consumers.
- Prometheus or Kubernetes metrics.
- The downstream DEBUG stream.
- The web UI.
- General CDP compatibility for Camoufox.
- Emulating rendering capabilities that Lightpanda does not have.
- Implementing the existing CDP HTTP discovery and target-management placeholders.
  They remain registered and explicitly unimplemented in this package. The supported
  contract is a direct WebSocket URL passed to `connect_over_cdp()`.

## Public contract

### Canonical connection endpoint

```text
WS /v1/connect
```

The canonical URL has no trailing slash. The route must also accept `/v1/connect/`
directly because WebSocket clients cannot reliably follow HTTP redirects. Both paths
execute the same handler.

These connections are equivalent:

```text
ws://harbor/v1/connect
ws://harbor/v1/connect?harbor.provider.slug=auto
```

An explicit provider override uses the same route:

```text
ws://harbor/v1/connect?harbor.provider.slug=chromium
ws://harbor/v1/connect?harbor.provider.slug=browserless
ws://harbor/v1/connect?harbor.provider.slug=lightpanda
ws://harbor/v1/connect?harbor.provider.slug=camoufox
```

`/v1/connect/{provider}` is removed. Harbor must not teach clients to encode browser
selection into their endpoint path.

### Query parameter rules

Harbor owns only query keys beginning with `harbor.`.

- Keys are case-sensitive.
- Duplicate Harbor keys are invalid, even when the values are identical.
- Unknown `harbor.*` keys are invalid.
- Empty values are invalid. Omission, not an empty string, requests the default.
- Values are case-sensitive lowercase identifiers.
- Non-Harbor query parameters are ignored and are not forwarded to an upstream
  provider in this package.
- Query parameters may select configured resources but must never contain provider or
  proxy credentials.

The only setting implemented in this package is:

| Key | Allowed values | Omitted value |
| --- | --- | --- |
| `harbor.provider.slug` | `auto`, `chromium`, `browserless`, `lightpanda`, `camoufox` | `auto` |

The settings registry is the canonical inventory of supported `harbor.*` keys. Each
setting definition owns a typed Pydantic schema, a query prefix, its defaults, and
whether it supports automatic resolution. Schema fields become dotted query keys. For
example, the `slug` field of the provider schema becomes `harbor.provider.slug`.

The resolver returns requested and resolved settings as distinct immutable objects:

```python
@dataclass(frozen=True, slots=True)
class RequestedSessionSettings:
    overrides: dict[str, Any]
    auto_fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedSessionSettings:
    provider: ProviderSettingSchema
    sources: dict[str, SettingSource]
```

Resolution precedence is explicit query override, automatic planner result, then schema
default. The requested and resolved values remain distinct for the entire session, and
each resolved field records its source. This is required for future decision logging:
`auto` is an input, while `chromium` is a decision. The planner resolves the complete
settings set in one call so future settings can be selected coherently rather than by
independent per-setting algorithms.

### Automatic provider policy

The initial planner always resolves `auto` to `chromium`:

```python
class HarborPlanner(Protocol):
    async def plan(
        self,
        context: SettingsResolutionContext,
        requested: RequestedSessionSettings,
        defaults: dict[str, Any],
    ) -> dict[str, Any]: ...
```

The planner sees the complete request and defaults so its choices can account for
interactions between settings. The resolver applies explicit overrides after planning,
so a caller's explicit value always wins. The resolved value is therefore produced in
one place for every connection.

This package does not fall back if Chromium is full or unavailable. It returns the
corresponding stable error. Later roadmap packages can replace the planner without
changing the route or settings registry.

## Session model

A Harbor session is the lifetime of one downstream WebSocket connection, beginning
before admission and ending after all provider resources and PostgreSQL capacity have been
released.

### States

```text
requested -> queued -> acquiring -> connected -> closing -> closed
     |          |           |           |            |
     +----------+-----------+-----------+------------+-> failed
```

Allowed transitions are:

| From | To |
| --- | --- |
| `requested` | `queued`, `acquiring`, `failed` |
| `queued` | `acquiring`, `failed` |
| `acquiring` | `connected`, `failed` |
| `connected` | `closing`, `failed` |
| `closing` | `closed`, `failed` |

`closed` and `failed` are terminal. A transition not listed above is a programming
error and must not be written to PostgreSQL.

### Identity and ownership

- `session_id`: UUID4 generated by Harbor before admission.
- `owner_id`: stable random UUID4 generated once when an API process starts.
- `lease_token`: UUID4 generated per acquisition attempt and used as a fencing token.

Only the holder of the current lease token may heartbeat, transition, or release an
active session. Release is idempotent.

### Session data

The PostgreSQL `gateway_sessions` row contains:

| Field | Meaning |
| --- | --- |
| `session_id` | Harbor session identifier |
| `owner_id` | API process currently handling the socket |
| `lease_token` | Fencing token for mutations |
| `state` | Current session state |
| `requested_provider` | Query value, including `auto` |
| `resolved_provider` | Concrete provider selected by the planner |
| `created_at` | Request creation time |
| `queued_at` | Queue entry time, if queued |
| `acquiring_at` | Capacity acquisition time |
| `connected_at` | Upstream connection completion time |
| `closed_at` | Terminal time |
| `lease_expires_at` | Current lease deadline for queued or active work |
| `queue_sequence` | Database sequence used for FIFO ordering |
| `terminal_reason` | Stable Harbor reason code, if terminal |

Times are timezone-aware PostgreSQL timestamps. Database server time is read inside
each admission transaction so replicas do not make queue or lease decisions using
different local clocks.

## State ownership

The rule for state is:

```text
Truly process-local state              -> memory
Transactional coordination and history -> PostgreSQL
Live notifications and future replay   -> NATS / JetStream
```

### In-memory state

Only the API process handling a connection stores:

- Downstream and upstream WebSocket objects.
- Relay tasks and cancellation primitives.
- The local handle associated with the PostgreSQL lease.
- Immutable adapter and capability-registry objects.
- Parsed request data before the PostgreSQL session row is created.

There is no in-memory fallback for admission or capacity. If PostgreSQL is unavailable,
Harbor fails closed because admitting sessions independently on multiple replicas
would violate capacity guarantees.

NATS capacity notifications are an optimization only. A missed notification or NATS
outage cannot admit too many sessions, reorder the queue, or leak capacity. Queued
connections fall back to periodic PostgreSQL claims until NATS returns.

### PostgreSQL tables

Package 1 owns three tables and one database sequence:

```text
gateway_provider_state
gateway_sessions
session_events
harbor_gateway_queue_sequence
```

`gateway_provider_state` contains one row per provider. The row exists to provide a
stable `SELECT FOR UPDATE` lock that serializes capacity decisions for that provider;
providers do not block each other.

`gateway_sessions` contains ownership, state, requested and resolved settings,
timestamps, queue sequence, and lease deadline. A global PostgreSQL sequence provides
FIFO order. `session_events` records lifecycle transitions in the same
transaction that changes the session row.

Schema changes are managed by Alembic. Application startup never creates tables. Local
Compose runs `alembic upgrade head` before starting the API; production deployments
must run migrations as a separate deployment step.

### Default operational settings

All values are environment-backed global settings in `backend/settings.py`:

| Settings field / environment variable | Default |
| --- | ---: |
| `session_lease_seconds` / `SESSION_LEASE_SECONDS` | 30 seconds |
| `session_heartbeat_seconds` / `SESSION_HEARTBEAT_SECONDS` | 10 seconds |
| `session_queue_poll_ms` / `SESSION_QUEUE_POLL_MS` | 1,000 milliseconds |
| `session_queue_timeout_seconds` / `SESSION_QUEUE_TIMEOUT_SECONDS` | 30 seconds |
| `provider_acquisition_timeout_seconds` / `PROVIDER_ACQUISITION_TIMEOUT_SECONDS` | 10 seconds |
| `nats_connect_timeout_seconds` / `NATS_CONNECT_TIMEOUT_SECONDS` | 1 second |

Provider capacity defaults are conservative for the current local containers:

| Provider | Active sessions | Queued sessions |
| --- | ---: | ---: |
| Chromium | 1 | 100 |
| Browserless | 5 | 100 |
| Lightpanda | 1 | 100 |
| Camoufox | 1 | 100 |

Production deployments must configure capacity to match the independently managed
provider fleet. All Harbor replicas in one deployment must use the same values.

### Transactional PostgreSQL operations

Admission, claiming, heartbeat, transition, and release use SQLAlchemy async sessions.
Each operation is one database transaction and reads PostgreSQL server time. Provider
admission and release lock the corresponding provider row before counting or changing
capacity.

#### Admit

In one transaction:

1. Lock the provider-state row.
2. Mark expired active and queued leases as `failed` with
   `session_lease_expired` and append their lifecycle events.
3. Insert the requested session and `session.requested` event.
4. Count unexpired active and queued sessions.
5. If no queue exists and capacity is available, transition to `acquiring`.
6. Otherwise, if queue capacity is available, transition to `queued`.
7. Otherwise, transition to `failed` with `provider_queue_full`.
8. Insert every resulting lifecycle event and commit atomically.

New requests never bypass an existing queue.

A queued row receives a deadline equal to the queue timeout plus one lease duration.
The waiting process does not heartbeat it. Cancellation and timeout mark it failed
immediately; lease expiry recovers a waiter whose Harbor process died.

#### Claim queue head

A queued request wakes on a Core NATS capacity notification or after the one-second
fallback interval. In one transaction it:

1. Locks the provider-state row.
2. Expires stale active and queued leases.
3. Confirms the supplied owner and fencing token.
4. Confirms the session is the FIFO queue head.
5. Confirms active capacity is available.
6. Transitions it to `acquiring`, creates its active lease, and appends the event.

Correctness never relies on Core NATS delivery. Notifications reduce latency; polling
bounds recovery when a notification is missed or NATS is unavailable.

#### Heartbeat

In one transaction:

1. Lock the session row.
2. Confirm `owner_id` and `lease_token` match.
3. Confirm the session is in an active state.
4. Extend `lease_expires_at` using database server time.

Failure means lease ownership has been lost. The local process closes both sockets and
must not attempt a second release under a stale token.

#### Release

In one transaction:

1. Confirm `owner_id` and `lease_token` match unless the session has already reached a
   terminal state.
2. Lock the provider row and session row.
3. Write `closed` or `failed`, its stable reason, and terminal timestamp.
4. Clear the lease deadline.
5. Append the terminal lifecycle event and commit.
6. Publish a best-effort NATS capacity notification after commit.

Running release more than once returns success without changing capacity twice.

### Package 2 event boundary

Every accepted lifecycle transition is already durable in PostgreSQL. Rows contain:

- event type
- session ID
- requested and resolved provider
- timestamp
- stable reason, when present

The table contains no URL, headers, cookies, CDP payloads, or credentials. Package 2
will publish the normalized event contract to JetStream for live fan-out and replay
without moving session coordination out of PostgreSQL.

## Internal architecture

The proxy package is reorganized as follows:

```text
backend/proxy/
├── adapters/
│   ├── browserless.py
│   ├── camoufox.py
│   ├── chromium.py
│   ├── lightpanda.py
│   └── registry.py
├── capabilities/
│   ├── manifests.py
│   └── registry.py
├── contracts/
│   ├── capability.py
│   ├── provider.py
│   ├── session.py
│   └── settings.py
├── postgres/
│   └── repository.py
├── sessions/
│   ├── capacity.py
│   └── manager.py
├── settings/
│   ├── base.py
│   ├── provider.py
│   ├── registry.py
│   └── resolver.py
├── transport/
│   ├── cdp.py
│   └── websocket.py
├── errors.py
├── gateway.py
└── planner.py
```

Responsibilities are fixed:

- API route: hand the WebSocket to `Gateway`; no provider logic.
- Settings registry: declare and validate every supported `harbor.*` key.
- Settings resolver: merge explicit values, planner output, and schema defaults.
- Planner: choose all automatic setting values from one resolution context.
- Session manager: own lifecycle, PostgreSQL admission, leases, and cleanup.
- Adapter: acquire and close one provider session.
- Capability registry: authorize downstream CDP methods for the resolved provider.
- CDP transport: decode commands, enforce capabilities, and relay messages.
- PostgreSQL repository: expose typed operations backed by row-locked transactions.
- Capacity notifier: wake queued replicas through NATS without owning correctness.

### Provider session contract

Adapters return a lease-like object rather than only a URL:

```python
class ProviderAdapter(Protocol):
    name: ProviderName

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ) -> ProviderSession: ...


class ProviderSession(Protocol):
    provider: ProviderName

    async def send(self, message: str | bytes) -> None: ...
    def messages(self) -> AsyncIterator[str | bytes]: ...
    async def close(self) -> None: ...
```

`close()` is idempotent. Provider-specific URLs, credentials, and transport overrides
remain inside the adapter.

## Connection lifecycle

For every downstream connection, Harbor performs this sequence:

1. Parse and validate all `harbor.*` query parameters.
2. Resolve requested settings through the planner.
3. Create a session ID, owner ID reference, and lease token.
4. Admit or enqueue the session atomically in PostgreSQL.
5. Wait for FIFO capacity until admitted or timed out.
6. Acquire the resolved provider within the acquisition timeout.
7. Start the lease heartbeat.
8. On success, accept the downstream WebSocket only after provider acquisition
   succeeds.
9. Run the capability-aware bidirectional transport.
10. When either side ends, cancel the opposite relay task.
11. Close the provider session.
12. Release PostgreSQL capacity exactly once and notify waiters through NATS.
13. Close the downstream WebSocket if it remains open.

A successful downstream socket is accepted only after provider acquisition. A
pre-session failure takes a separate accept-and-immediately-close path solely to
deliver the stable private WebSocket close code. No provider fallback occurs.

Application shutdown stops accepting new sessions, closes locally owned sockets, and
releases their leases. A hard pod termination is recovered by the 30-second lease
expiry.

## Capability model

Provider support is default-deny:

> A provider/command pair is unsupported until a provider-specific test proves it and
> its manifest explicitly enables it.

Each provider has an immutable manifest containing exact CDP method names. Missing
entries return `NotSupported`; presence authorizes either passthrough or mapping.

```python
@dataclass(frozen=True, slots=True)
class Capability:
    method: str
    handling: Literal["passthrough", "mapped"]
```

- Chromium, Browserless, and Lightpanda use `passthrough` only for approved methods.
- Camoufox uses `mapped` only for implemented methods.
- An approved command still fails truthfully if the provider rejects valid input.
- Upstream events needed to complete an approved behavior are covered by that
  behavior's conformance test. Testing only the command response is insufficient.
- A command added to one provider is not implicitly enabled for another.

The CDP transport parses downstream JSON objects. A message with a method not present
in the selected provider manifest is not sent upstream. Harbor responds on the same
session with:

```json
{
  "id": 42,
  "error": {
    "code": -32601,
    "message": "Runtime.evaluate is not supported by provider lightpanda"
  }
}
```

The WebSocket remains open after this response. Malformed JSON or a malformed CDP
command is a protocol violation and closes the connection with the stable
`invalid_cdp_message` reason. CDP uses text JSON messages; a downstream binary message
is also `invalid_cdp_message`. An upstream binary message is an upstream protocol
failure and closes the session as `provider_connection_lost`.

### Initial command baseline

The baseline was captured on 2026-07-16 from the repository's three examples using
Playwright 1.61.0 against the local Chromium provider. Each method below remains denied
for a provider until that provider's conformance test passes:

```text
Browser.getVersion
Browser.getWindowForTarget
Browser.setDownloadBehavior
Browser.setWindowBounds
DOM.getContentQuads
DOM.scrollIntoViewIfNeeded
Emulation.setDeviceMetricsOverride
Emulation.setEmulatedMedia
Emulation.setFocusEmulationEnabled
Input.dispatchMouseEvent
Log.enable
Network.enable
Page.addScriptToEvaluateOnNewDocument
Page.createIsolatedWorld
Page.enable
Page.getFrameTree
Page.navigate
Page.setLifecycleEventsEnabled
Runtime.callFunctionOn
Runtime.enable
Runtime.evaluate
Runtime.releaseObject
Runtime.runIfWaitingForDebugger
Target.createBrowserContext
Target.createTarget
Target.getTargetInfo
Target.setAutoAttach
```

The observed event baseline includes Target attachment, Runtime execution-context,
Page frame/lifecycle, and Network request/response/loading events. Protocol recordings
used as fixtures must normalize IDs, timestamps, temporary paths, browser versions,
and execution-context identifiers before they are committed.

When a Playwright upgrade introduces a new bootstrap method, tests fail with
`NotSupported` until the method is deliberately verified and added. This is expected
behavior, not an automatic passthrough condition.

## Provider implementations

### Chromium

- Discover the upstream browser WebSocket through `/json/version`.
- Connect using the configured transport host and port rather than an advertised
  container-local hostname.
- Admit one session at a time by default.
- Close pages and browser contexts created by the Harbor session during cleanup.
- Do not claim cross-session process isolation; the current local container is one
  shared browser process.

### Browserless

- Connect through its configured native CDP WebSocket.
- Keep provider query details and credentials inside the adapter.
- Limit Harbor to five concurrent sessions by default, matching local Compose.
- Treat Browserless queue and timeout failures as provider acquisition failures;
  Harbor's PostgreSQL queue is the public queue.
- Closing the provider session must end the corresponding Browserless session.

### Lightpanda

- Connect through its configured native CDP WebSocket.
- Admit one session at a time by default.
- Preserve truthful upstream errors for its one-context and one-page limitations.
- Do not approve screenshots, PDF output, or other graphical-layout behavior.

### Camoufox

Camoufox speaks Playwright Firefox/Juggler rather than CDP. Its adapter connects using
the server's Playwright endpoint and exposes a Harbor-owned virtual CDP browser.

The first mapping is bounded to the three downstream examples:

1. Connect and create the initial browser context/page.
2. Navigate and return response status information.
3. Return serialized page HTML through the Runtime operations Playwright emits.
4. Locate and click the example link through the DOM/Input operations Playwright
   emits.
5. Emit the target, execution-context, frame, lifecycle, and network events Playwright
   waits for.
6. Evaluate JavaScript and serialize its result.
7. Close the virtual targets, Playwright page/context, and provider connection.

The facade maintains virtual browser, context, target, CDP session, frame, execution
context, and remote-object IDs for one downstream Harbor session. IDs never cross
Harbor sessions. Unsupported methods return the standard CDP error without closing the
socket.

Passing these examples establishes only the tested vertical slice; it does not make
Camoufox generally CDP compatible.

## Errors

All errors are Harbor domain errors before they are rendered for a transport. Error
messages never include credentials, upstream URLs, container hostnames, database details, or
tracebacks.

### Before CDP transport starts

Harbor accepts the WebSocket and immediately closes it with a private application code
when a connection cannot start:

| Close code | Reason |
| ---: | --- |
| `4400` | `invalid_harbor_settings` |
| `4408` | `session_queue_timeout` |
| `4429` | `provider_queue_full` |
| `4503` | `provider_unavailable` |
| `4504` | `provider_acquisition_timeout` |
| `1011` | `harbor_internal_error` |

PostgreSQL unavailability maps to `provider_unavailable` in this package because Harbor
cannot safely admit a distributed session without its transactional coordinator. Detailed causes are logged
internally.

### After CDP transport starts

- Unsupported method: CDP error `-32601`; keep the socket open.
- Provider rejects an approved method: forward its CDP error unchanged.
- Malformed CDP message: close with `4400`, reason `invalid_cdp_message`.
- Unexpected upstream disconnect: close with `1011`, reason
  `provider_connection_lost`.
- Lost PostgreSQL lease: close with `1011`, reason `session_lease_lost`.
- Normal downstream close: close upstream, release capacity, and record `closed`.

## Testing strategy

### Unit tests

- Query parsing, omission semantics, duplicate keys, unknown keys, and invalid values.
- `auto` and explicit planner resolution.
- Every allowed and forbidden session-state transition.
- Provider adapter acquisition and idempotent cleanup.
- Capability registry default-deny behavior.
- CDP unsupported-method response without disconnection.
- Text and binary WebSocket forwarding.
- Relay cancellation when either side terminates.
- Stable error mapping and sanitization.
- Camoufox virtual ID isolation and individual command mappings.

### PostgreSQL integration tests

Run against the Compose PostgreSQL service and use two independent sessions
instances to represent separate Harbor replicas:

- Immediate admission below capacity.
- FIFO ordering once a queue exists.
- No new request bypasses a queued request.
- Queue capacity enforcement.
- Queue timeout removes the waiter.
- Cancelled clients are removed from the queue.
- Failed provider acquisition releases capacity.
- Concurrent final-slot claims admit exactly one session.
- Duplicate release changes capacity only once.
- A stale lease token cannot heartbeat, transition, or release a replacement lease.
- A killed owner's capacity becomes available no later than the lease duration.
- PostgreSQL loss during an active session closes the session rather than continuing
  without ownership.
- State transition and lifecycle event row are written atomically.

### Provider conformance tests

Tests are parameterized by provider but approval remains explicit in each provider
manifest. A green Chromium test does not approve another provider.

The initial behavior cases are:

| Behavior | Example |
| --- | --- |
| Connect, navigate, retrieve content | `01_goto_and_content.py` |
| Navigate, locate, click, await navigation | `02_interaction.py` |
| Navigate and evaluate JavaScript | `03_evaluate.py` |

The required matrix is:

| Example | Chromium | Browserless | Lightpanda | Camoufox |
| --- | ---: | ---: | ---: | ---: |
| goto + content | required | required | required | required |
| link interaction | required | required | required | required |
| JavaScript evaluation | required | required | required | required |

Every test connects to `/v1/connect` and changes only `harbor.provider.slug`. A
separate run omits the setting and proves that `auto` currently selects Chromium.

### Lifecycle end-to-end tests

Using Docker Compose:

- Ten sequential sessions leave no active or queued PostgreSQL rows.
- Two clients competing for a capacity-one provider are served FIFO.
- Client cancellation while queued removes the queue entry.
- Client cancellation during navigation closes the provider session and releases
  capacity.
- Provider termination during a session produces the stable failure and releases
  capacity.
- Terminating the owning Harbor container allows another replica to reclaim capacity
  after lease expiry.
- All examples use only the standard Playwright client and contain no Harbor SDK code.

## Implementation sequence

Implementation proceeds in this order; every step lands with its tests:

1. Replace provider path routing with `/v1/connect` and the typed settings registry.
2. Add requested/resolved setting contracts and the static planner.
3. Add PostgreSQL lifecycle wiring and Alembic migrations.
4. Implement the session model, row-locked repository transactions, NATS capacity
   notifications, and multi-session PostgreSQL integration tests.
5. Replace `connect()` URL results with acquired provider-session contracts.
6. Separate gateway orchestration from WebSocket/CDP transport.
7. Implement default-deny capability manifests and CDP error rendering.
8. Capture, normalize, and commit protocol fixtures for the three examples.
9. Approve and verify the baseline independently for Chromium, Browserless, and
   Lightpanda.
10. Build the Camoufox virtual CDP facade one tested behavior at a time.
11. Add Compose lifecycle and two-replica HA tests.
12. Update `README.md`, `examples/README.md`, and `docs/PROVIDERS.md` to describe only
    verified behavior.

## Definition of done

This work package is complete when all of the following are true:

- The public browser connection is `/v1/connect` with optional `harbor.*` settings.
- Omitting `harbor.provider.slug` is identical to specifying
  `harbor.provider.slug=auto`.
- The first automatic plan deterministically resolves to Chromium.
- All four explicit provider overrides use the same route.
- Invalid, duplicate, and unknown Harbor settings fail consistently.
- All shared session, queue, capacity, and lease state is held in PostgreSQL.
- Multiple Harbor replicas cannot exceed configured provider capacity.
- Pod death cannot leak capacity beyond the 30-second lease duration.
- PostgreSQL failure never causes an unsafe in-memory admission fallback.
- NATS failure degrades queue wakeups to bounded polling without changing correctness.
- Every provider begins with no enabled CDP methods.
- Every enabled method has a provider-specific conformance test and explicit manifest
  entry.
- Unsupported methods return `-32601` without terminating a healthy CDP session.
- The unchanged three downstream examples pass through Harbor for all four providers.
- Camoufox exposes only its tested mapping slice and makes no general CDP claim.
- Disconnects, cancellations, acquisition failures, timeouts, lease loss, and shutdown
  all release capacity exactly once.
- Tests cover two independent Harbor replicas contending through PostgreSQL.
- Documentation and the provider matrix match the behavior proven by tests.
