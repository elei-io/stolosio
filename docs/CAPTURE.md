# Page capture

`POST /v1/capture` answers "give me this page": the page's content as a person would receive it, at
the lowest cost that achieves it, or a failure that says why. The algorithm and its contract live in
the `pagecapture` workspace package ([contract](../packages/pagecapture/docs/api.md), JSON examples in
[`contract/`](../packages/pagecapture/contract)). Stolosio hosts it on its own capacity, egress and
network policy. `WS /v1/connect` stays the endpoint for real browser automation.

```http
POST /v1/capture
Content-Type: application/json

{"url": "https://example.com/", "accept": ["text/html"], "exclusions": [{"host": "*.ads.example"}]}
```

| Status | Meaning |
| --- | --- |
| 200 | Every capture result, `captured` or `failed` |
| 400 | An invalid request, including a `url` its own exclusions cover |
| 503 + `Retry-After` | No capacity to start the capture, or a database conflict that outlasted retries; the body is a `failed` result with failure code `capacity` |

There is no authentication: the endpoint is for callers inside the deployment's network.

## Capacity

A capture consumes global logical-session capacity before it fetches HTTP. It acquires
one local `browserless` slot only when verification, managed rendering, or local challenge
resolution needs a browser. Verified HTTP cache hits and non-HTML responses require no
provider attempt and continue under browser saturation. They incur no modeled browser-slot cost.

Provider acquisition uses the existing transactional queue and leaves at least five seconds
of the tier's remaining budget for rendering. Managed and local resolution reuse the assigned
slot until completion; paid resolution replaces it, or acquires cloud capacity directly if
local acquisition was refused. Queue limits and cancellation cleanup still apply.

Global admission refusals, admission deadline expiry, and exhausted admission database conflicts
return 503. Network-policy lookup and logical admission share the caller's deadline; late
admissions are drained and released. Once HTTP
acquisition has begun, browser capacity refusals are completed capture results (HTTP 200,
failure code `capacity`, with retry guidance and the HTTP body as failure evidence).
The caller's deadline is capped by `CAPTURE_DEFAULT_DEADLINE_MS`. The remaining deadline
bounds fetching, classification, method-cache access, provider
waits and rendering. Expiry returns `deadline_exceeded`, retaining already-fetched content.
Bounded event recording and capacity cleanup run before the response, outside the acquisition budget.

## Egress and network policy

The plain fetch goes through the egress proxy (`HTTP_FETCH_PROXY_URL`) only, never the API
process's own network; the proxy's own error answers (and a refused `CONNECT`) fail the capture as
`unreachable` (transient), or as `host_not_found` (permanent) when the API's resolver confirms the
host has no such name or no address: the proxy's DNS failure alone can be a resolver hiccup. Stolosio's
network policy joins the request's exclusions, so a blocked domain is refused on every redirect hop
and for every browser request (`excluded`). Browsers enforce exclusions through the CDP Fetch
domain, which also catches redirect hops.

## Challenge resolution

When `resolve_bot_challenges` is true, bot challenges and bot block pages first try
`local_resolution` on a lazily acquired
local fleet slot. The initial resolver is deliberately simple: a fresh browser context using the
browser's native user agent. Images, fonts and media are permitted, with a transfer cap;
service workers remain blocked to preserve URL exclusions. The initial 10-second challenge wait
extends up to 25 seconds when progress is observed, with 35 seconds for the entire local attempt (also bounded by the capture deadline). It has no external
solver fee; local capacity and browser time still count. All local requests enforce exclusions and
network policy, and returned content is re-assessed before acceptance.

If protection holds or the local resolver is unavailable, paid fallback is allowed only when
`resolve_bot_challenges` is true and an operator has enabled `browserless_cloud`
(`PATCH /v1/admin/providers/browserless_cloud/capacity`; needs `BROWSERLESS_CLOUD_TOKEN`). This flag
gates both local and paid resolution. When false, neither resolver runs. The capture trades its local slot for a
cloud attempt, counted against that provider's concurrency limit, and Browserless BrowserQL
unblocks through a residential proxy (`BROWSERLESS_CLOUD_PROXY_COUNTRY`, default `jp`). No cloud
capacity fails as `capacity` (transient). Provider-side navigation retains its existing exclusion
limitation: exclusions apply after CDP handover, not to redirect hops during BrowserQL navigation.

Evidence identifies `direct`, `managed`, `local_resolution`, and paid `challenge_resolution`
attempts, including each assessment, decision, duration, and reason for escalation. A bot failure's
`resolution_attempted` includes local attempts. Neither resolution tier repeats within a capture.

### Local demonstration

With the development Compose stack running:

```bash
STOLOSIO_E2E=1 uv run pytest tests/e2e/test_capture_e2e.py -k local_resolution -q -s
```

The test creates a temporary Docker origin on a Docker-only public-address subnet (special-use
addresses are correctly denied by egress). It connects the local proxy and browser to that network,
then removes the origin and network on teardown without relaxing the firewall. It demonstrates a
JavaScript challenge clearing locally when resolution is enabled, complete content
returned without paid usage, and challenges failing without any solver attempt
when resolution is disabled. This verifies plumbing;
it makes no claim about solving real CAPTCHA providers. Paid-fallback ordering and capacity are
covered separately by package and Postgres integration tests.

External capacity refusals include Stolosio limits (`retry_after_seconds` 5), or Browserless refusing for the plan's limits — HTTP 429 or a BrowserQL
error naming a concurrency or rate limit (Retry-After, else 30 s), HTTP 402 or an error naming a
quota, units or billing (Retry-After, else 3600 s). Any other BrowserQL HTTP error, unreadable
response or connection failure is an outage: `browser_unavailable`. Every BrowserQL failure logs a
warning with its HTTP status, duration and a single-line summary of at most 200 characters with the
token and URL queries redacted; failure messages carry the same summary.

## State and accounting

HTML that merely passes classification still requires a trustworthy render or method-cache evidence before
it can be accepted. If verification is refused or returns an empty page, capture fails with transient
`incomplete_content` and retains the plain HTML as failure evidence. Browser outages, capacity limits,
deadlines and bot protection keep their specific failure codes. Unverified HTML is never promoted to
success because a browser attempt failed, and failed verification adds no method-cache evidence.

- The method cache (`capture_method_cache`) remembers, per URL and URL pattern, where rendering
  confirmed that plain HTTP is enough. Evidence updates are atomic; contradicted exact URLs
  require rendering until their evidence expires. Keys preserve the origin, path and raw query.
  Clear development method-cache entries when updating to the origin-aware key format. It is the only thing Stolosio learns about sites; the
  maintenance worker purges entries unseen for `CAPTURE_METHOD_CACHE_RETENTION_DAYS` (30).
- Every capture writes a `capture.completed` outbox event: outcome, failure code and category,
  tiers used, duration, browser seconds, whether a paid tier was used, bytes, and bounded
  acquisition steps (tier, status, assessment code, decision, cache/verification reason, HTTP
  coverage). Free-text decisions, URL cache keys, renderer notes and page data are not retained.
  The summary is saved on the capture session in the same transaction as its outbox event.
- A failed capture logs a warning with its failure code, category, transience and session id;
  never the URL or the failure message, which may carry credentials.
- Metrics: `stolosio_captures_total{outcome,category,tier}`, `stolosio_capture_rejected_total{reason}`,
  `stolosio_capture_duration_seconds{tier}`, `stolosio_capture_browser_seconds_total{tier}`,
  `stolosio_capture_paid_total`.

## Operator views

Overview gives automation sessions and captures equal prominence. Captures has its own
searchable, paginated history and acquisition timeline. HTTP-only means no browser tier was
attempted; a render which verifies HTTP sufficiency still counts as browser use even when the
result returns `response_body`. The HTTP-only share uses successfully captured pages as its
denominator. Failure, admission rejection, interruption and unavailable-result states are
separate. Rejections before a database transaction commits (including exhausted database
conflicts) are observable through counters, not durable session history.

Capture summaries follow terminal-session retention, independently of event-feed retention.
They expose only the requested hostname, never raw URLs, query values, arbitrary caller
references, headers or documents. No per-site performance claims are inferred from failures.

## Acquisition analytics

Stolosio records one durable, idempotent fact per completed capture, independently of
DEBUG retention. `GET /v1/admin/captures/overview?window=7d` (also `24h`, `30d`, `90d`)
feeds the admin Captures page. The four outcomes are `default` (normal acquisition),
`internally_resolved`, `externally_resolved`, and `total_failure`. Successful attribution
follows the document returned: a retained plain response stays `default` even if a
resolver was attempted and failed. A paid attempt is supplier usage, not proof of
external success.

The overview reports counts and rates for all completed captures and for captures
with detected bot protection and resolution enabled. It includes resolver attempt
counts, time, paid usage and mean capture duration. Empty cohorts have null rates.
Admission refusals and captures interrupted before completion are outside this denominator.
Tracking starts with the new table; historical DEBUG data is not backfilled. No URL,
page body, cookies or credentials are stored in this projection. Acquisition acceptance
does not establish downstream usefulness; Periplus owns that assessment and its customer
usage accounting. The public capture response retains its existing attempt evidence;
the four analytics labels are internal to Stolosio.
