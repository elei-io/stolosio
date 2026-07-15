# Dependable Browser Gateway

Status: implementation-ready design  
Roadmap package: 1  
Target: the first dependable Harbor session gateway

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
- Own session admission, queuing, capacity, leases, and terminal cleanup in Redis so
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
- Postgres analytics models or Redis-to-Postgres consumers.
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
ws://harbor/v1/connect?harbor.provider=auto
```

An explicit provider override uses the same route:

```text
ws://harbor/v1/connect?harbor.provider=chromium
ws://harbor/v1/connect?harbor.provider=browserless
ws://harbor/v1/connect?harbor.provider=lightpanda
ws://harbor/v1/connect?harbor.provider=camoufox
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
| `harbor.provider` | `auto`, `chromium`, `browserless`, `lightpanda`, `camoufox` | `auto` |

The parser returns two immutable objects:

```python
@dataclass(frozen=True, slots=True)
class RequestedSessionSettings:
    provider: ProviderSelection


@dataclass(frozen=True, slots=True)
class ResolvedSessionSettings:
    provider: ProviderName
```

The requested and resolved values remain distinct for the entire session. This is
required for future decision logging: `auto` is an input, while `chromium` is a
decision.

### Automatic provider policy

The initial planner always resolves `auto` to `chromium`:

```python
class SessionPlanner(Protocol):
    async def resolve(
        self,
        requested: RequestedSessionSettings,
    ) -> ResolvedSessionSettings: ...
```

An explicit provider bypasses automatic selection but still passes through the
planner. The resolved value is therefore produced in one place for every connection.

This package does not fall back if Chromium is full or unavailable. It returns the
corresponding stable error. Later roadmap packages can replace the planner without
changing the route or settings parser.

## Session model

A Harbor session is the lifetime of one downstream WebSocket connection, beginning
before admission and ending after all provider resources and Redis capacity have been
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
error and must not be written to Redis.

### Identity and ownership

- `session_id`: UUID4 generated by Harbor before admission.
- `owner_id`: stable random UUID4 generated once when an API process starts.
- `lease_token`: UUID4 generated per acquisition attempt and used as a fencing token.

Only the holder of the current lease token may heartbeat, transition, or release an
active session. Release is idempotent.

### Session data

The Redis session hash contains:

| Field | Meaning |
| --- | --- |
| `session_id` | Harbor session identifier |
| `owner_id` | API process currently handling the socket |
| `lease_token` | Fencing token for mutations |
| `state` | Current session state |
| `requested_provider` | Query value, including `auto` |
| `resolved_provider` | Concrete provider selected by the planner |
| `created_at_ms` | Request creation time |
| `queued_at_ms` | Queue entry time, if queued |
| `acquiring_at_ms` | Capacity acquisition time |
| `connected_at_ms` | Upstream connection completion time |
| `closed_at_ms` | Terminal time |
| `terminal_reason` | Stable Harbor reason code, if terminal |

Times use Unix epoch milliseconds from Harbor. Redis server time is used inside atomic
scripts for queue and lease comparisons so replicas do not make admission decisions
using different local clocks.

## State ownership

The rule for state is:

```text
Truly process-local state          -> memory
Shared ephemeral operational state -> Redis
Durable analytical history         -> Postgres
Persistent user-facing config       -> Postgres
```

### In-memory state

Only the API process handling a connection stores:

- Downstream and upstream WebSocket objects.
- Relay tasks and cancellation primitives.
- The local handle associated with the Redis lease.
- Immutable adapter and capability-registry objects.
- Parsed request data before the Redis session is created.

There is no in-memory fallback for admission or capacity. If Redis is unavailable,
Harbor fails closed because admitting sessions independently on multiple replicas
would violate capacity guarantees.

This package requires one logical Redis primary, optionally protected by replication
and Sentinel or supplied as a managed HA service. Redis Cluster sharding is not
supported because each admission script atomically updates session, provider, and
event-stream keys. Harbor API replicas are horizontally scalable; Redis is a shared
coordination dependency rather than state embedded in an API pod.

### Redis keys

All keys are versioned so the layout can change without ambiguous mixed deployments:

```text
harbor:v1:sessions:{session_id}              HASH
harbor:v1:providers:{provider}:active        ZSET
harbor:v1:providers:{provider}:queue         ZSET
harbor:v1:providers:{provider}:queue_sequence STRING
harbor:v1:session_events                     STREAM
```

The active sorted-set member is `session_id`; its score is the lease expiry in Unix
milliseconds. The queue member is `session_id`; its score is a monotonically
increasing sequence produced with `INCR`. Sequence scores provide FIFO ordering without
depending on replica clocks. UUID lexical order breaks ties, although ties should not
occur.

### Default operational settings

All values are environment-backed global settings in `backend/settings.py`:

| Settings field / environment variable | Default |
| --- | ---: |
| `session_lease_seconds` / `SESSION_LEASE_SECONDS` | 30 seconds |
| `session_heartbeat_seconds` / `SESSION_HEARTBEAT_SECONDS` | 10 seconds |
| `session_queue_poll_ms` / `SESSION_QUEUE_POLL_MS` | 100 milliseconds |
| `session_queue_timeout_seconds` / `SESSION_QUEUE_TIMEOUT_SECONDS` | 30 seconds |
| `provider_acquisition_timeout_seconds` / `PROVIDER_ACQUISITION_TIMEOUT_SECONDS` | 10 seconds |
| `terminal_session_ttl_seconds` / `TERMINAL_SESSION_TTL_SECONDS` | 3,600 seconds |
| `session_event_stream_maxlen` / `SESSION_EVENT_STREAM_MAXLEN` | 100,000 |

Provider capacity defaults are conservative for the current local containers:

| Provider | Active sessions | Queued sessions |
| --- | ---: | ---: |
| Chromium | 1 | 100 |
| Browserless | 5 | 100 |
| Lightpanda | 1 | 100 |
| Camoufox | 1 | 100 |

Production deployments must configure capacity to match the independently managed
provider fleet. All Harbor replicas in one deployment must use the same values.

### Atomic Redis operations

Admission, claiming, heartbeat, and release are implemented as checked-in Lua scripts
invoked through the async Redis client.

#### Admit

In one script:

1. Remove expired members from the provider active set.
2. Remove queued members whose session hash no longer exists.
3. Create the session hash and its lease token.
4. If the queue is empty and active capacity is available, add the session to active
   and transition it to `acquiring`.
5. Otherwise, if queue capacity is available, allocate a queue sequence, append the
   session, and transition it to `queued`.
6. Otherwise, transition it to `failed` with `provider_queue_full`.
7. Append the transition to the session event stream.

New requests never bypass an existing queue.

A queued session hash expires after the queue timeout plus one lease duration. The
waiting process does not heartbeat it. Each queue poll verifies the hash still exists;
disconnect and timeout cleanup remove it immediately, while expiry recovers a waiter
whose Harbor process died.

#### Claim queue head

A queued request polls with 100 ms delay plus up to 20 ms random jitter. In one script:

1. Prune expired active members and stale queued members.
2. Confirm the supplied session is the queue head.
3. Confirm active capacity is available.
4. Remove it from the queue, add it to active with a 30-second lease, and transition it
   to `acquiring`.

Polling avoids relying on Redis Pub/Sub, whose notifications can be lost during a pod
restart. The queue timeout bounds polling.

#### Heartbeat

In one script:

1. Confirm the session hash exists.
2. Confirm `owner_id` and `lease_token` match.
3. Confirm the session is in an active state.
4. Extend the active-set score and session-hash expiry.

Failure means lease ownership has been lost. The local process closes both sockets and
must not attempt a second release under a stale token.

#### Release

In one script:

1. Confirm `owner_id` and `lease_token` match unless the session has already reached a
   terminal state.
2. Remove the session from both the active and queue sets.
3. Write `closed` or `failed`, its stable reason, and `closed_at_ms`.
4. Set terminal retention to one hour.
5. Append the terminal transition to the event stream.

Running release more than once returns success without changing capacity twice.

### Future Postgres ingestion boundary

Every accepted Redis transition is appended to `harbor:v1:session_events` in the same
Lua script that changes the session state. Stream entries contain:

- event type
- session ID
- requested and resolved provider
- timestamp
- stable reason, when present

The stream contains no URL, headers, cookies, CDP payloads, or credentials. A later
analytics package may consume completed session events into Postgres in batches. No
Postgres writer is implemented here.

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
├── redis/
│   ├── repository.py
│   └── scripts/
│       ├── admit.lua
│       ├── claim.lua
│       ├── heartbeat.lua
│       ├── release.lua
│       └── transition.lua
├── sessions/
│   ├── capacity.py
│   └── manager.py
├── transport/
│   ├── cdp.py
│   └── websocket.py
├── errors.py
├── gateway.py
├── planner.py
└── settings_parser.py
```

Responsibilities are fixed:

- API route: hand the WebSocket to `Gateway`; no provider logic.
- Settings parser: validate `harbor.*` keys and create requested settings.
- Planner: turn requested settings into resolved settings.
- Session manager: own lifecycle, Redis admission, leases, and cleanup.
- Adapter: acquire and close one provider session.
- Capability registry: authorize downstream CDP methods for the resolved provider.
- CDP transport: decode commands, enforce capabilities, and relay messages.
- Redis repository: expose typed operations backed by atomic scripts.

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
4. Admit or enqueue the session atomically in Redis.
5. Wait for FIFO capacity until admitted or timed out.
6. Acquire the resolved provider within the acquisition timeout.
7. Start the lease heartbeat.
8. On success, accept the downstream WebSocket only after provider acquisition
   succeeds.
9. Run the capability-aware bidirectional transport.
10. When either side ends, cancel the opposite relay task.
11. Close the provider session.
12. Release Redis capacity exactly once.
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
  Harbor's Redis queue is the public queue.
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
messages never include credentials, upstream URLs, container hostnames, Redis keys, or
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

Redis unavailability maps to `provider_unavailable` in this package because Harbor
cannot safely admit a distributed session without Redis. Detailed causes are logged
internally.

### After CDP transport starts

- Unsupported method: CDP error `-32601`; keep the socket open.
- Provider rejects an approved method: forward its CDP error unchanged.
- Malformed CDP message: close with `4400`, reason `invalid_cdp_message`.
- Unexpected upstream disconnect: close with `1011`, reason
  `provider_connection_lost`.
- Lost Redis lease: close with `1011`, reason `session_lease_lost`.
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

### Redis integration tests

Run against the Compose Redis service and use two independent repository/client
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
- Redis loss during an active session closes the session rather than continuing
  without ownership.
- State transition and stream event are written atomically.

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

Every test connects to `/v1/connect` and changes only `harbor.provider`. A separate run
omits the setting and proves that `auto` currently selects Chromium.

### Lifecycle end-to-end tests

Using Docker Compose:

- Ten sequential sessions leave no active or queued Redis members.
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

1. Replace provider path routing with `/v1/connect` and the typed settings parser.
2. Add requested/resolved setting contracts and the static planner.
3. Add Redis lifecycle wiring to FastAPI startup and shutdown.
4. Implement the session model, Redis repository, Lua scripts, and multi-client Redis
   integration tests.
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
- Omitting `harbor.provider` is identical to specifying `harbor.provider=auto`.
- The first automatic plan deterministically resolves to Chromium.
- All four explicit provider overrides use the same route.
- Invalid, duplicate, and unknown Harbor settings fail consistently.
- All shared session, queue, capacity, and lease state is held in Redis.
- Multiple Harbor replicas cannot exceed configured provider capacity.
- Pod death cannot leak capacity beyond the 30-second lease duration.
- Redis failure never causes an unsafe in-memory admission fallback.
- Every provider begins with no enabled CDP methods.
- Every enabled method has a provider-specific conformance test and explicit manifest
  entry.
- Unsupported methods return `-32601` without terminating a healthy CDP session.
- The unchanged three downstream examples pass through Harbor for all four providers.
- Camoufox exposes only its tested mapping slice and makes no general CDP claim.
- Disconnects, cancellations, acquisition failures, timeouts, lease loss, and shutdown
  all release capacity exactly once.
- Tests cover two independent Harbor replicas contending through Redis.
- Documentation and the provider matrix match the behavior proven by tests.
