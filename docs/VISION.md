# Harbor Vision

Harbor is a personal browser and web-acquisition fleet. It gives automation clients a
single endpoint through which they can acquire, use, observe, and release browser
sessions without coupling themselves to a particular browser implementation or runtime
platform.

## Product promise

Harbor aims to be a CDP-compatible browser gateway:

> Change the browser endpoint, retain the automation, and gain browser routing and fleet
> management.

A client already using Puppeteer, a CDP library, or Playwright's `connect_over_cdp()`
should be able to replace its browser URL with a Harbor URL and continue using its
existing automation. The selected provider can then be changed through the URL or by a
Harbor routing policy:

```text
wss://harbor.example/v1/connect/chromium
wss://harbor.example/v1/connect/browserless
wss://harbor.example/v1/connect/lightpanda
wss://harbor.example/v1/connect/camoufox
wss://harbor.example/v1/connect/auto
```

Harbor will target the common 80 percent of browser automation behavior across all
providers. Provider-specific and uncommon commands may not work everywhere initially,
but unsupported behavior must fail explicitly and predictably rather than hang or
silently produce an incorrect result.

Harbor does not promise that every provider behaves exactly like Chromium. It promises
that ordinary CDP automation can be portable when the selected provider has the
required capabilities.

## Initial providers

Harbor begins with four providers:

- Plain Chromium provides direct CDP control and serves as the behavioral baseline.
- Browserless Chromium provides CDP with managed browser lifecycle, concurrency, and
  queueing.
- Lightpanda provides a lightweight, CDP-compatible browser optimized for automation.
- Camoufox provides a Firefox-based, anti-detection browser through Playwright's
  Firefox/Juggler protocol. Harbor translates the portable CDP surface for this
  provider.

The provider is an implementation detail for clients that remain within Harbor's
portable capability surface. Clients may still select a provider explicitly when a task
requires its particular performance, compatibility, or stealth characteristics.

## Initial scope

Harbor will:

- Provide clients with isolated browser sessions.
- Route sessions to explicitly selected providers or an automatic selection policy.
- Expose per-provider queue and session metrics suitable as horizontal scaling targets.
- Normalize browser and provider quirks behind a CDP-compatible gateway.
- Track provider capabilities and report unsupported operations clearly.
- Preserve native CDP passthrough where possible.
- Own session authentication, authorization, lifecycle, and cleanup.

Browser processes are local Docker Compose dependencies during development. In
production, browsers may be managed by Kubernetes or another external platform. Harbor
provides the demand and scaling signals rather than requiring ownership of the runtime
orchestrator.

## Longer-term direction

Harbor will later:

- Learn to optimize browser placement and resource usage from historical data.
- Build revisable domain-level routing knowledge from historical session observations.
- Select providers based on capabilities, queue pressure, cost, performance, and past
  success.
- Provide a standardized live debugging stream across providers.
- Provide a web interface for fleet monitoring and session debugging.
- Make provider health, compatibility, and scaling behavior observable over time.

## Design principles

### Adoption without rewrites

Existing CDP automation should require an endpoint change, not a new automation SDK.

### Honest portability

Harbor exposes capabilities and explicit protocol errors. It does not pretend that a
provider supports behavior that cannot be implemented faithfully.

### Progressive compatibility

Compatibility expands domain by domain and command by command. Native passthrough gives
CDP providers broad coverage immediately; translated providers begin with high-value
operations and grow from observed usage.

### External orchestration

Harbor separates browser acquisition and protocol routing from infrastructure scaling.
It emits the signals an external platform needs to scale browser capacity.
