# Providers

Stolosio has three active acquisition providers:

| Provider | Role | Capacity owner |
| --- | --- | --- |
| HTTP | Bounded no-browser navigation and HTML retrieval | Stolosio admission |
| Browserless | Default browser path and Stolosio-managed horizontal fleet | Stolosio instances and slots |
| Browserbase | Premium external browser path for difficult sites | Stolosio's configured external quota |

Browserless and Browserbase expose Chrome DevTools Protocol. Stolosio treats their CDP
traffic as opaque protocol transport: it preserves command IDs, session IDs, event
order, backpressure, and close behavior, but does not maintain a method-by-method
capability list or translate command semantics.

A provider that needs a CDP translation layer is not an active Stolosio provider.

## Session ownership

One Stolosio browser attempt owns one upstream browser session. Several Stolosio sessions
may occupy independent slots on one Browserless worker, but Stolosio does not share one
upstream browser session between them.

Browserless capacity is instances multiplied by configured session slots per instance.
Stolosio assigns slots and its separate fleet controller scales workers. Browserless is
not assumed to own Stolosio's horizontal scaling policy.

The development Browserless fleet permits a browser job to run for 10 minutes, while
Stolosio permits 30 seconds for browser acquisition and startup. An upstream close at
the configured session deadline is recorded as `provider_timeout`; unexpected early
closes remain `provider_connection_lost`.

Browserbase capacity is an administrator-controlled concurrent-session limit. It can
mirror a subscription allowance or be set lower as a cost guardrail. Stolosio applies
the limit transactionally before creating a Browserbase session. For automatic
routing, capacity alone is not permission to spend: the session must also include
`stolosio.provider.allow_paid_fallback=true`.

## Promotion and escalation

Background promotion probes HTTP and Browserless and uses their navigation, status,
header, and content results to improve future domain plans. Browserbase is never
probed automatically; it is assumed to work as the terminal provider. An operator may
explicitly run a Browserbase probe from the domain UI when the diagnostic value
justifies its cost. A generic "probe all" action remains limited to HTTP and
Browserless.

Automatic sessions may begin on HTTP. An unsupported command or failed origin
status, header, response-size, or content check causes immediate live escalation.
Proxy failures and network-policy denials are terminal and cannot trigger escalation.
Successful transitions contribute compact domain-level evidence, allowing Browserless
to become the preferred starting provider after a small number of browser-required
sessions and later move back toward HTTP when compatible evidence accumulates.

Browserbase is never selected as the primary automatic provider. It is attempted only
after local candidates are exhausted, global Browserbase capacity is enabled, and the
session explicitly permits paid fallback. Direct
`stolosio.provider.slug=browserbase` selection remains available and is still subject to
admission limits. Once a browser has been acquired, Stolosio forwards all CDP commands
to it and does not escalate again.

Explicit HTTP sessions return a protocol error for unsupported commands. Browser
providers return their own CDP success or error without Stolosio claiming support.

## Global request blocking

Browserless and Browserbase attempts receive the same operator-managed domain
blocklist through a Stolosio-owned CDP target bootstrap. Policy injection is required:
if a provider cannot apply it, Stolosio reports `domain_blocking_unavailable` instead
of silently running unblocked. The HTTP provider enforces the list on its navigation
and every redirect and has no subresource requests to filter. See
[Network policy](NETWORK_POLICY.md).

## Time and cost observations

Stolosio records slot occupancy, upstream browser-connected time, provider-reported
browser time, estimated billable time, and aggregated per-method
end-to-end/provider latency where available. It does not retain completed commands
individually. Browserless modeled cost uses slot occupancy; Browserbase modeled cost
uses estimated billable time including its minimum. The rate and basis are captured
on the finalized attempt. One bounded method summary is stored transactionally when
each attempt ends and folded into cumulative
provider-and-method counts, failures, interruptions, latency, attributed browser
time, and attributed cost. Retained method identity is capped per provider and excess
identities fold into `__other__`.
Concurrent command durations are capped by the attempt's measured browser time, with
the residual recorded as unattributed session time. A separate bounded attempt phase
summary measures command-active union time and fixed internal lifecycle phases without
changing provider behavior or the cumulative attribution.
