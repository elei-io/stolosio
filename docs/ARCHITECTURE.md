# Harbor Architecture

## Overview

Harbor presents a CDP-compatible edge to downstream automation clients and routes each
session to one of several browser providers. Providers that already speak CDP use a
session-aware passthrough path. Providers that use another protocol are served through a
stateful translation bridge.

```text
Puppeteer / Playwright connect_over_cdp / CDP client
                         |
                         v
                Harbor CDP gateway
          discovery, auth, routing, metrics
                         |
             +-----------+-----------+
             |                       |
             v                       v
      Native CDP proxy       CDP translation bridge
             |                       |
   +---------+---------+             v
   |         |         |      Playwright/Juggler
   v         v         v             |
Chromium  Browserless  Lightpanda    v
                                  Camoufox
```

## External connection contract

Harbor's implemented connection contract is a direct provider-neutral WebSocket:

```text
WS /v1/connect
WS /v1/connect?harbor.provider.slug=camoufox
WS /v1/connect?harbor.session.reference=<client-generated-uuid>
```

Omitting `harbor.provider.slug` is equivalent to
`harbor.provider.slug=auto`. Every Harbor-owned connection setting uses the
`harbor.*` prefix, and an explicit value overrides the automatic plan. Internal
provider addresses never reach clients because doing so would bypass authorization,
metrics, normalization, and session cleanup.

The registered HTTP discovery and target-management routes remain unimplemented. When
implemented, they use the same provider-neutral paths and `harbor.*` settings rather
than placing a provider in the URL path.

Authentication may also carry tenant policy and provider constraints, but browser
selection remains an explicit query override when a caller requests a particular
provider.

`harbor.session.reference` is optional correlation metadata for clients using the
separate DEBUG WebSocket. Harbor stores it but retains authority over its internal
session identity.

## Session lifecycle

Harbor has two independent lifecycles. A logical session represents the downstream CDP
connection and consumes global Harbor capacity:

```text
requested -> admitted -> open -> closing -> closed
     |          |         |
     +----------+---------+-> failed
```

An acquisition attempt represents one concrete execution choice:

```text
requested -> queued -> acquiring -> active -> completed
     |          |          |          |
     +----------+----------+----------+-> failed
```

The session owns:

- Authentication and tenant identity.
- Requested connection settings.
- The downstream WebSocket, lease, and global admission slot.
- Cleanup state and termination reason.

Attempts own resolved provider settings, provider queue position, acquisition state,
provider resources, and outcome. HTTP-to-browser promotion creates another attempt
under the same logical session after closing the HTTP attempt as `promoted`.

PostgreSQL transactions own global session admission and separate per-provider FIFO
admission. NATS capacity notifications wake queued attempt handlers; PostgreSQL polling
is the correctness fallback. The API replica retains its downstream socket throughout.

NATS Core provides live fan-out. JetStream stores normalized observations for durable
consumers and replay. A maintenance process records those observations idempotently in
PostgreSQL, updates factual domain and command-use projections, forwards lifecycle
outbox rows, and removes expired detail in bounded batches.

The gateway watches downstream disconnect while provider admission is pending. One
component owns WebSocket acceptance, denial, and closure. Provider and session cleanup
are bounded and idempotent.

## Fleet control plane

Harbor manages browser workers as provider fleets. A fleet contains compatible browser
instances, and each instance exposes one or more session slots. Provider attempts queue
at fleet level and atomically reserve a slot on a ready, non-draining instance before
the adapter connects directly to that instance.

Desired fleet configuration and observed instance state are durable in PostgreSQL.
Fleet controllers run separately from FastAPI and reconcile that state through Docker,
Kubernetes, or another infrastructure platform. Desired replicas never count as usable
capacity until their instances have been observed healthy and ready.

Administrators control minimum and maximum instances, per-instance session capacity,
and scaling safety limits. These settings are control-plane policy and cannot be
overridden by downstream `harbor.*` connection parameters. Lower limits drain capacity
without silently terminating successful sessions.

See [Fleet Management](FLEET_MANAGEMENT.md) for the design contract and
[Managed Fleets](roadmap/managed-fleets.md) for the first implementation milestone.

Fleet reconciliation has three boundaries: a provider-neutral reconciler owns desired
state convergence, provider definitions describe endpoint facts, and runtime drivers
perform infrastructure operations. Chromium and Lightpanda use the same reconciler
through the local Docker Compose driver. A Kubernetes or k3s driver replaces only the
infrastructure operations, not scaling policy, PostgreSQL state, placement, or provider
adapters.

## Provider adapters

Each provider adapter implements acquisition, connection, health, capability reporting,
and cleanup. Adapters do not define Harbor's public protocol; they satisfy the gateway's
internal provider contract.

### Plain Chromium

Plain Chromium is the reference implementation. Harbor acquires a CDP endpoint and
proxies commands and events with minimal transformation.

### Browserless Chromium

Browserless is CDP-compatible but owns additional queueing and lifecycle behavior. Its
adapter translates Browserless connection and session conventions into Harbor lifecycle
and metrics while retaining native CDP passthrough.

### Lightpanda

Lightpanda exposes CDP but implements a subset of Chromium and Web Platform behavior.
The adapter uses passthrough for supported commands, reports known capabilities, and
normalizes unsupported-method failures where possible.

### Camoufox

Camoufox is Firefox-based and exposes Playwright's Firefox/Juggler protocol, not CDP.
Its adapter uses a stateful CDP-to-Playwright bridge. Camoufox remote serving is
experimental, so its health and failure behavior must be isolated from the gateway.

## CDP gateway modes

### Native passthrough

For Chromium, Browserless, and Lightpanda, Harbor forwards JSON CDP messages and events
over a controlled upstream WebSocket. The proxy may still intercept selected commands
to enforce policy, maintain session state, normalize errors, or collect metrics.

Passthrough must preserve:

- Command IDs and corresponding responses.
- Target session IDs and flattened-session routing.
- Event order within a connection.
- Backpressure between client and provider sockets.
- Close codes and meaningful protocol errors.

### Adaptive no-browser execution

An automatic session begins behind a bounded CDP facade without acquiring a provider.
The facade supports the tested one-page Playwright bootstrap, performs `Page.navigate`
through HTTP, and answers only the exact recognized `page.content()` protocol shape.
Every other sequence acquires the configured fallback provider.

Before the triggering command runs, Harbor replays every command already acknowledged
by the facade in original order, maps facade-owned identifiers to acquired-provider
identifiers, waits for response and lifecycle catch-up, and suppresses duplicate replay
output. The downstream WebSocket and logical session do not change. PostgreSQL records
the attempts plus factual per-domain promotion history; the replay log itself remains
bounded and process-local.

See [No-Browser Execution](NO_BROWSER.md) and the implemented
[No-Browser Promotion](roadmap/no-browser-promotion.md) milestone.

### Deterministic provider qualification

Automatic sessions remain providerless until `Page.navigate` reveals a domain. Unknown
and unqualified domains acquire the operator-selected default. Qualified domains use the
provider with the lowest historical average attempt cost, falling back to its configured
cost rate.

After eligible completed sessions, a separate worker runs cheaper `goto` plus `content`
probes through the same adaptive gateway path. PostgreSQL owns probe jobs, leases,
configuration, and qualification state. The existing DEBUG/JetStream path owns detailed
evidence; there is no second analytical event stream.

See [Deterministic Domain Routing](ANALYTICS.md).

### Translated execution

For Camoufox, the gateway terminates CDP and becomes the protocol authority. It maps CDP
commands to Playwright operations and synthesizes CDP responses and events.

The initial 80/20 compatibility surface should prioritize:

- `Target`: browser contexts, pages, target discovery, attachment, and closure.
- `Page`: navigation, reload, lifecycle events, frames, content, and screenshots.
- `Runtime`: evaluation, console and exception events, object handles, and release.
- `DOM`: document retrieval, selectors, attributes, text, and basic mutation.
- `Input`: keyboard, mouse, wheel, and touch input where supported.
- `Network`: requests, responses, headers, cookies, and interception.
- `Browser`: version, contexts, and download behavior.
- `Emulation`: viewport, locale, timezone, geolocation, and supported identity settings.

The bridge maintains mappings for:

```text
Harbor target ID       <-> Playwright page
Harbor browserContext  <-> Playwright browser context
Harbor frame ID        <-> Playwright frame
Harbor execution ID    <-> execution world/context
Harbor node ID         <-> DOM handle
Harbor object ID       <-> JavaScript handle
Harbor request ID      <-> Playwright request
Harbor CDP session ID  <-> attached target session
```

Mappings are scoped to one Harbor session and released during explicit disposal,
target closure, or session cleanup.

## Errors and unsupported behavior

An unsupported command returns a CDP protocol error rather than disconnecting or
silently succeeding:

```json
{
  "id": 42,
  "error": {
    "code": -32601,
    "message": "Method Page.printToPDF is not supported by provider camoufox"
  }
}
```

Harbor should distinguish:

- Unknown CDP methods.
- Methods known to Harbor but unsupported by the selected provider.
- Invalid parameters.
- Invalid or expired object, node, target, and session identifiers.
- Provider acquisition failures.
- Provider crashes and upstream disconnections.
- Policy and authorization failures.

Provider-specific errors should be normalized without discarding diagnostic context.

## Capabilities

Harbor maintains a capability profile for each provider and acquired session. The
profile combines static adapter knowledge with runtime discovery and version data.

Capabilities should be granular enough for routing decisions, for example:

```json
{
  "provider": "lightpanda",
  "native_protocol": "cdp",
  "capabilities": {
    "javascript": true,
    "screenshots": false,
    "downloads": false,
    "network_interception": true
  }
}
```

Automatic routing must reject providers missing required capabilities before a session
becomes active. Explicit provider selection may connect with reduced capabilities, but
the gateway must return clear errors for unsupported operations.

## Metrics and scaling signals

Metrics use bounded provider, outcome, method-registry, and stable-reason labels:

- Globally admitted Harbor sessions and configured intake capacity.
- Provider attempts queued, acquiring, active, failed, and completed.
- Queue depth and oldest queued request age.
- Acquisition and queue latency.
- Session duration and idle time.
- Provider capacity and health.
- Commands, events, bytes, errors, and unsupported commands.
- Provider crashes and abnormal disconnects.

The primary scaling signals are queue depth, oldest request age, occupied slots relative
to healthy capacity, and acquisition latency. Harbor's fleet policy converts those
signals into desired browser capacity. A separate platform controller reconciles that
desired state through Docker, Kubernetes, or another runtime.

`GET /v1/fleet/gateway`, `GET /v1/fleet/providers`, and `/metrics` use PostgreSQL fleet
snapshot queries.
The JSON view reports current counts; Prometheus remains responsible for historical
rates and quantiles. Across multiple API replicas, shared PostgreSQL-backed gauges are
aggregated with `max`, while process-local counters and histograms are summed.

## Debugging

The proxy observes the downstream and upstream protocol streams. It publishes a
versioned normalized event contract to session-addressable NATS subjects captured by
JetStream. Live DEBUG consumers subscribe through Core NATS while the maintenance
recorder and later analytical consumers use independent durable JetStream consumers.

Filtering happens before publication: bodies, HTML, cookies, credentials, query values,
raw protocol messages, and provider error text do not enter the event system. Historical
DEBUG reads the same normalized rows stored by the recorder and adds no conclusions.

The initial downstream delivery path is
`WS /v1/debug?harbor.session.reference=<uuid>`. It uses an ephemeral ordered JetStream
consumer to replay retained events and follow the live tail for one session. It is
read-only, bounded, and kept separate from CDP so existing CDP clients never receive
Harbor-specific protocol events.

## Compatibility strategy

Compatibility is measured from real command usage rather than assumed from domain
names. Harbor should record unsupported commands and translation failures, aggregate
them without sensitive payloads, and use the results to prioritize bridge coverage.

Native CDP providers remain the reference for conformance tests. A shared suite should
run representative CDP workflows against all providers and document intentional
differences. Translation features are complete only when both command responses and
associated event sequences behave consistently enough for existing CDP clients.
