# Stolosio Vision

Stolosio is a personal browser and web-acquisition fleet. It gives automation clients a
single endpoint through which they can acquire, use, observe, and release browser
sessions without coupling themselves to a particular browser implementation or runtime
platform.

## Product promise

Stolosio aims to be a CDP-compatible browser gateway:

> Change the browser endpoint, retain the automation, and gain browser routing and fleet
> management.

A client already using Puppeteer, a CDP library, or Playwright's `connect_over_cdp()`
should be able to replace its browser URL with a Stolosio URL and continue using its
existing automation. The selected provider can then be changed through the URL or by a
Stolosio routing policy:

```text
wss://stolosio.example/v1/connect
wss://stolosio.example/v1/connect?stolosio.provider.slug=http
wss://stolosio.example/v1/connect?stolosio.provider.slug=browserless
wss://stolosio.example/v1/connect?stolosio.provider.slug=browserbase
```

Stolosio will target the common 80 percent of browser automation behavior across all
providers. Provider-specific and uncommon commands may not work everywhere initially,
but unsupported behavior must fail explicitly and predictably rather than hang or
silently produce an incorrect result.

Native browser providers relay CDP without method-by-method mappings. The bounded HTTP
facade supports only its explicitly documented bootstrap surface and escalates an
automatic session to a browser for every other operation.

## Initial providers

Stolosio begins with three acquisition paths:

- Plain HTTP provides bounded navigation and content retrieval without occupying a
  browser.
- Browserless provides native CDP through a Stolosio-managed, horizontally scalable
  fleet with explicit per-instance session capacity.
- Browserbase provides native CDP as the externally managed terminal fallback for
  difficult sites, with Stolosio-owned concurrency and queue limits.

The provider is an implementation detail for clients that remain within Stolosio's
portable capability surface. Clients may still select a provider explicitly when a task
requires its particular performance, compatibility, or stealth characteristics.

## Initial scope

Stolosio will:

- Provide clients with isolated browser sessions.
- Route sessions to explicitly selected providers or an automatic selection policy.
- Pack isolated sessions into compatible browser instances and scale managed provider
  fleets from measured demand.
- Expose per-provider demand, capacity, health, and scaling metrics.
- Preserve opaque native CDP passthrough for browser providers.
- Report operations outside the bounded HTTP facade explicitly.
- Own session authentication, authorization, lifecycle, and cleanup.

Browser processes are local Docker Compose dependencies during development. Stolosio
owns their desired capacity, placement, health, and draining through a separate fleet
controller. Docker, Kubernetes, or another runtime supplies the compute primitives.

## Longer-term direction

Stolosio will later:

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

Stolosio exposes capabilities and explicit protocol errors. It does not pretend that a
provider supports behavior that cannot be implemented faithfully.

### Progressive compatibility

Compatibility expands domain by domain and command by command. Native CDP providers
use passthrough only for explicitly verified methods; translated providers begin with
high-value operations and grow from observed usage.

### Managed fleets

Stolosio owns browser fleet policy without embedding infrastructure credentials in the
gateway. Separate controllers reconcile Stolosio's desired state through Docker,
Kubernetes, or another platform. Administrators control fleet limits; downstream
clients remain unaware of browser instances and capacity.
