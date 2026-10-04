# Providers

Stolosio has two acquisition providers:

| Provider | Role | Capacity owner |
| --- | --- | --- |
| Browserless (`browserless`) | Default `/v1/connect` provider, `/v1/capture`'s render tier, and Stolosio-managed horizontal fleet | Stolosio instances and slots |
| Browserless cloud (`browserless_cloud`) | Paid stealth browsers behind residential proxies: on `/v1/connect` when a client names it, and as `/v1/capture`'s challenge-resolution tier | Stolosio's configured external quota |

A `/v1/connect` session uses the provider named by `stolosio.provider.slug`, or
`browserless` when the setting is omitted. Stolosio never chooses a provider on the
client's behalf and never moves a session to another provider. `/v1/capture` chooses its
own method; see [Page capture](CAPTURE.md).

Both providers expose Chrome DevTools Protocol. Stolosio treats their CDP traffic as
opaque protocol transport: it preserves command IDs, session IDs, event order,
backpressure, and close behavior, but does not maintain a method-by-method capability
list or translate command semantics.

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

Browserless cloud is used only when named. A `/v1/connect` client names it with
`stolosio.provider.slug=browserless_cloud` and may pick the residential exit country with
`stolosio.browserless.proxy_country` (default `BROWSERLESS_CLOUD_PROXY_COUNTRY`, `jp`). It is
disabled until an operator enables it, which needs `BROWSERLESS_CLOUD_TOKEN`; its concurrent-session
limit (seeded at 15) bounds both endpoints. Browserless's pricing page (checked 2026-10-02) lists
the Prototyping plan as "10 max concurrent browsers+5" without saying what the "+5" is, and
Browserless cloud exposes no account, usage or `/pressure` endpoint to read the plan's limit (only
`/meta`, the version). When Browserless itself refuses (429, 402, or a BrowserQL error naming a
concurrency, rate or quota limit), the capture fails as `capacity`, never `browser_unavailable`
(`docs/CAPTURE.md`); the API log line names the HTTP status, so a limit set above the plan shows
up there, and the limit should then be lowered to the plan's base concurrency. It can mirror a
subscription allowance or be set lower as a cost guardrail, and Stolosio applies it
transactionally before connecting. Cloud browsers sit outside
Stolosio's egress firewall, so the network policy is applied in the browser.

Browser providers return their own CDP success or error without Stolosio claiming
support.

## Global request blocking

Browserless and Browserless cloud attempts receive the same operator-managed domain
blocklist through a Stolosio-owned CDP target bootstrap. Policy injection is required:
if a provider cannot apply it, Stolosio reports `domain_blocking_unavailable` instead
of silently running unblocked. In `/v1/connect` sessions the list filters a page's own
requests (ads, trackers), not navigations to a listed host; capture also refuses those
navigations, and its plain HTTP fetch enforces the list on every redirect hop. See
[Network policy](NETWORK_POLICY.md).

## Time and cost observations

Stolosio records slot occupancy, upstream browser-connected time, provider-reported
browser time, chargeable time, and aggregated per-method
end-to-end/provider latency where available. It does not retain completed commands
individually. Every attempt's modeled cost is its slot occupancy (capacity-occupied
time, from acquisition to release) times its provider's rate in cost units per second.
Operators manage the rates on the admin Settings page (`GET /v1/admin/costs/rates`,
`PATCH /v1/admin/costs/rates/{provider}`); startup seeds `browserless` at 100 and
`browserless_cloud` at 300. The rate and basis are captured on the finalized attempt. One bounded method summary is stored transactionally when
each attempt ends and folded into cumulative
provider-and-method counts, failures, interruptions, latency, attributed browser
time, and attributed cost. Retained method identity is capped per provider and excess
identities fold into `__other__`.
Concurrent command durations are capped by the attempt's measured browser time, with
the residual recorded as unattributed session time. A separate bounded attempt phase
summary measures command-active union time and fixed internal lifecycle phases without
changing provider behavior or the cumulative attribution.

## Unsupported discovery endpoints

The CDP WebSocket entrypoint is `/v1/connect`. The registered HTTP discovery/target
management and `/v1/connect/devtools/*` WebSocket routes return HTTP 501 with
`unsupported_endpoint` before accepting a connection. They do not expose provider
addresses or claim support for browser discovery.
