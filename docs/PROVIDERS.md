# Providers

Harbor has three active acquisition providers:

| Provider | Role | Capacity owner |
| --- | --- | --- |
| HTTP | Bounded no-browser navigation and HTML retrieval | Harbor admission |
| Browserless | Default browser path and Harbor-managed horizontal fleet | Harbor instances and slots |
| Browserbase | Premium external browser path for difficult sites | Harbor's configured external quota |

Browserless and Browserbase expose Chrome DevTools Protocol. Harbor treats their CDP
traffic as opaque protocol transport: it preserves command IDs, session IDs, event
order, backpressure, and close behavior, but does not maintain a method-by-method
capability list or translate command semantics.

A provider that needs a CDP translation layer is not an active Harbor provider.

## Session ownership

One Harbor browser attempt owns one upstream browser session. Several Harbor sessions
may occupy independent slots on one Browserless worker, but Harbor does not share one
upstream browser session between them.

Browserless capacity is instances multiplied by configured session slots per instance.
Harbor assigns slots and its separate fleet controller scales workers. Browserless is
not assumed to own Harbor's horizontal scaling policy.

The development Browserless fleet permits a browser job to run for 10 minutes, while
Harbor permits 30 seconds for browser acquisition and startup. An upstream close at
the configured session deadline is recorded as `provider_timeout`; unexpected early
closes remain `provider_connection_lost`.

Browserbase capacity is an administrator-controlled concurrent-session limit. It can
mirror a subscription allowance or be set lower as a cost guardrail. Harbor applies
the limit transactionally before creating a Browserbase session. For automatic
routing, capacity alone is not permission to spend: the session must also include
`harbor.provider.allow_paid_fallback=true`.

## Promotion and escalation

Background promotion probes HTTP and Browserless and uses their navigation, status,
header, and content results to improve future domain plans. Browserbase is never
probed automatically; it is assumed to work as the terminal provider. An operator may
explicitly run a Browserbase probe from the domain UI when the diagnostic value
justifies its cost. A generic "probe all" action remains limited to HTTP and
Browserless.

Automatic sessions may begin on HTTP. An unsupported command or failed HTTP transport,
status, header, response-size, or content check causes immediate live escalation.
Successful transitions contribute compact domain-level evidence, allowing Browserless
to become the preferred starting provider after a small number of browser-required
sessions and later move back toward HTTP when compatible evidence accumulates.

Browserbase is never selected as the primary automatic provider. It is attempted only
after local candidates are exhausted, global Browserbase capacity is enabled, and the
session explicitly permits paid fallback. Direct
`harbor.provider.slug=browserbase` selection remains available and is still subject to
admission limits. Once a browser has been acquired, Harbor forwards all CDP commands
to it and does not escalate again.

Explicit HTTP sessions return a protocol error for unsupported commands. Browser
providers return their own CDP success or error without Harbor claiming support.

## Global request blocking

Browserless and Browserbase attempts receive the same operator-managed domain
blocklist through a Harbor-owned CDP target bootstrap. Policy injection is required:
if a provider cannot apply it, Harbor reports `domain_blocking_unavailable` instead
of silently running unblocked. The HTTP provider enforces the list on its top-level
navigation and has no subresource requests to filter. See
[Network policy](NETWORK_POLICY.md).

## Time and cost observations

Harbor records slot occupancy, upstream browser-connected time, provider-reported
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
unattributed connect, idle, and shutdown time recorded as session overhead.
