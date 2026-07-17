# Debug Stream

Harbor provides an opinionated, standardized stream of session observations.

The stream is used internally for understanding sessions and comparing provider
behavior. Harbor also exposes the same filtered event contract to its initial
downstream integration partner through a small, read-only WebSocket.

## Purpose

The stream preserves useful evidence from a session regardless of whether the work was
performed by plain HTTP, Chromium, Browserless, Lightpanda, or Camoufox.

This evidence should help Harbor's operators, and potentially downstream consumers,
investigate questions such as:

- Whether the selected provider behaved well for the session.
- Whether the observed responses indicate that a different level of browser stealth
  may be worth trying.
- Whether the observed network behavior makes IP rotation worth considering.
- Why a navigation or browser command failed.

The stream provides the observations needed to investigate these questions. It does not
answer them.

## Observations, not recommendations

The debug stream reports facts observed during the session. It must not emit decisions,
recommendations, or inferred conclusions.

For example, Harbor may report:

- An HTTP response returned status `403`.
- A response included a `Retry-After` header.
- A navigation redirected to another URL.
- A page had a particular title.
- A request failed because a proxy connection was refused.
- A browser process or page crashed.
- A console message or uncaught JavaScript error occurred.

Harbor must not turn those observations into statements such as:

- The session needs more stealth.
- The IP should be rotated.
- The provider was a bad choice.
- A provider change is required.
- A challenge or CAPTCHA was detected unless that fact was explicitly reported by the
  provider or remote system rather than inferred by Harbor.

Interpretation belongs to the human or system consuming the stream, not to the debug
stream itself.

## Opinionated filtering

Provider protocols expose large, noisy, and provider-specific event streams. Harbor
should not forward those streams unchanged.

"Opinionated" means Harbor selects the subset of browser-provided evidence that is
useful for understanding session behavior, removes protocol noise, and normalizes
equivalent observations across providers. It does not mean Harbor invents opinions
about what the observations imply.

Potential evidence includes:

- Navigation lifecycle and resulting URLs.
- Requests, response statuses, headers, MIME types, resource types, and timing.
- Redirect chains.
- Failed requests and transport errors.
- Page load and DOM-ready timing.
- Console messages and uncaught JavaScript errors.
- Frame creation and navigation.
- Browser dialogs and downloads.
- Page or browser crashes.
- Provider connections closing.
- Cookie changes where the provider exposes them.
- Equivalent HTTP response observations for no-browser sessions.

This list identifies evidence worth evaluating. It does not define the stream's schema
or promise that every provider can supply every observation.

## Standardization

Harbor should normalize equivalent evidence so consumers do not need to understand CDP,
Playwright/Juggler, Browserless conventions, Lightpanda differences, or the HTTP
no-browser path.

The useful common subset must be discovered from the evidence each provider actually
supplies. Provider-specific evidence may be retained when it is useful, but it must be
clearly distinguishable from observations available across providers.

Phase 2 now defines the first deliberately small, versioned event envelope and filters
main-document navigation, command outcomes, page lifecycle, provider disconnection,
provider attempts, and Harbor session lifecycle into it. The checked-in registry is
the contract; unknown event types and payload fields are rejected. URLs lose user
information, query strings, and fragments, and response headers use a strict
allowlist before an event reaches NATS.

Live internal consumers subscribe to
`harbor.v1.events.session.<session_id>`. JetStream retains the same publication for
durable consumers, and PostgreSQL supplies factual historical timelines. There is no
separate, richer raw stream behind this view.

Harbor also records `page.content_observed` with content length, plus `console.message`
and `javascript.exception` with bounded source, level, and message fingerprint. HTML
and unrestricted console text never enter the stream. Attempt closure includes
normalized Harbor cost units.

Whether a provider supports a domain and how providers are ordered are policy
conclusions stored outside DEBUG.

## Downstream WebSocket

A downstream client generates a UUID and supplies it while connecting to CDP:

```text
WS /v1/connect?harbor.session.reference=<uuid>
```

It observes that session through:

```text
WS /v1/debug?harbor.session.reference=<same-uuid>
```

The client reference correlates two connections; it is not Harbor's authoritative
session ID and is not an authentication token. Harbor stores a globally unique
reference on the session and continues to generate its own session UUID.

The DEBUG WebSocket can connect before the CDP connection. It waits up to 30 seconds
for the reference to appear. Once resolved, an ephemeral ordered JetStream consumer
replays retained events for that exact session and continues with the live tail. Each
WebSocket text frame is the canonical `SessionEvent` JSON object. The connection closes
normally after `session.closed` or `session.failed`.

Delivery is bounded to 1,000 pending events and 4 MiB per connection by default. A
consumer that falls behind is disconnected with `debug_consumer_too_slow`; it never
applies backpressure to browser execution.

This first public slice is deliberately single-session and read-only. Multi-session UI
subscriptions, authentication, authorization scopes, cursors beyond JetStream's
retention window, and public delivery guarantees remain future work. Until
authentication exists, the endpoint must be kept on a trusted network. Client
references must not be treated as secrets or access controls.

This initial schema is not a promise to add every item in the potential-evidence list.
The useful subset continues to grow one observed, normalized, privacy-tested provider
fact at a time.

## Operator activity feed

The administrative UI reads retained cross-session history from PostgreSQL:

```text
GET /v1/admin/events
```

It follows new events through a server-sent event stream backed by the existing
JetStream observation stream:

```text
GET /v1/admin/events/stream
```

Both endpoints accept repeated exact-match filters for provider, event family, event
type, and outcome, plus exact session and attempt IDs. The history endpoint uses an
opaque pagination cursor. SSE event IDs are JetStream stream sequences, so the browser
can resume through `Last-Event-ID`; filtered connections receive bounded cursor
heartbeats so their resume position continues to advance.

The activity feed does not introduce a richer or less-redacted event source. It exposes
the same validated `SessionEvent` facts with derived family and outcome fields for
display. History and live delivery are bounded independently, slow consumers never
backpressure browser execution, and the UI deduplicates events by `event_id`.

These routes are an operator contract, not a replacement for the downstream
single-session DEBUG WebSocket. Until administrative authentication exists, they must
remain on a trusted network.

Deterministic interpretation of these observations is described in
[Domain Routing](ANALYTICS.md).
