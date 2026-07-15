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

Harbor supports the HTTP discovery behavior expected by CDP clients as well as direct
WebSocket connections. A provider route may expose:

```text
GET /v1/connect/{provider}/json/version
GET /v1/connect/{provider}/json/list
GET /v1/connect/{provider}/json/protocol
WS  /v1/connect/{provider}/devtools/browser/{session_id}
```

The discovery responses always contain Harbor URLs. Internal provider addresses must
not leak to clients because doing so would bypass authorization, metrics, normalization,
and session cleanup.

`{provider}` initially accepts `chromium`, `browserless`, `lightpanda`, `camoufox`, and
`auto`. The `auto` route selects a provider from requested capabilities, availability,
policy, and queue pressure.

Authentication may also carry tenant policy and provider constraints, but browser
selection remains explicit in the path when a caller requests a particular provider.

## Session lifecycle

A connection progresses through these states:

```text
requested -> queued -> acquiring -> active -> closing -> closed
                         |            |
                         v            v
                       failed       expired
```

The gateway creates a Harbor session before acquiring provider capacity. The session
owns:

- Authentication and tenant identity.
- Requested and resolved provider.
- Queue and acquisition timestamps.
- Provider connection details.
- Protocol and capability information.
- Browser contexts and targets created through the connection.
- Activity and expiry timestamps.
- Cleanup state and termination reason.

Redis is intended for ephemeral session coordination, leases, and queue state.
PostgreSQL stores durable session history, provider observations, and later the data
used for placement optimization.

The proxy must close provider resources when a client disconnects, a lease expires, or
an acquisition fails. Cleanup must be idempotent.

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

Metrics are emitted per provider and, where useful, per tenant or capability class:

- Sessions requested, queued, acquiring, active, failed, and closed.
- Queue depth and oldest queued request age.
- Acquisition and queue latency.
- Session duration and idle time.
- Provider capacity and health.
- Commands, events, bytes, errors, and unsupported commands.
- Provider crashes and abnormal disconnects.

The primary horizontal scaling signals are queue depth, oldest request age, active
sessions relative to capacity, and acquisition latency. Harbor exposes these signals;
Kubernetes or another external platform decides how browser workloads scale.

## Debugging

The proxy observes the downstream and upstream protocol streams. It can therefore emit
a normalized debug stream containing session lifecycle, commands, responses, browser
events, network activity, console output, and provider failures.

Sensitive content must be redacted according to policy before storage or fan-out. The
debug abstraction must not delay the primary protocol path; slow debug consumers require
bounded buffers and explicit event-dropping behavior.

## Compatibility strategy

Compatibility is measured from real command usage rather than assumed from domain
names. Harbor should record unsupported commands and translation failures, aggregate
them without sensitive payloads, and use the results to prioritize bridge coverage.

Native CDP providers remain the reference for conformance tests. A shared suite should
run representative CDP workflows against all providers and document intentional
differences. Translation features are complete only when both command responses and
associated event sequences behave consistently enough for existing CDP clients.
