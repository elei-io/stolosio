# Capture API (schema v2)

`POST /v1/capture` returns a page's content as a person would receive it — a **trustworthy document** — at the
lowest cost that achieves it, or a **total failure** that says why. It is designed to live in **stolosio**, next to
its (simplified) CDP gateway for browser automation; **periplus** and other crawlers call it once per URL.

- **One call, one answer.** The caller passes a URL. Plain fetching, browser rendering, waiting, scrolling and
  escalation are decided and coordinated by the service, which returns evidence of what it did.
- **Trustworthy or failed.** `captured` means the service trusts the document holds the page's content. Anything
  short of that is `failed`, with the best-effort document attached as evidence.
- **One attempt per call, no retry decisions.** Retrying, backoff, per-domain pacing, robots.txt, deduplication,
  recrawl and storage belong to the caller. The response gives what it needs: a failure category, whether the
  failure is transient, and when to retry.
- **The caller's preferences travel with each request.** What it never wants fetched (`exclusions`) and what it
  stores (`accept`). The service keeps no per-caller settings.
- **No documents stored.** The document is returned inline. The service keeps only a small method cache (per URL
  and URL pattern: was plain HTTP enough last time?) to decide when rendering can be skipped.

The reference implementation is `pagecapture.CaptureService` (`src/pagecapture/service.py`); the types are in
`src/pagecapture/api.py` and `CaptureResult.to_json()` produces the response body below. `contract/` holds JSON
examples built from those types (a request, a capture, three failures, and every failure code with its category),
kept current by `tests/test_contract_fixtures.py`: clients test their parsing against them.

## Request

```http
POST /v1/capture
Content-Type: application/json

{
  "url": "https://jp.mercari.com/en/search?category_id=97",
  "resolve_bot_challenges": false,
  "exclusions": [{"host": "*.ads.example.com", "path_prefix": "/"}, {"host": "*", "path_prefix": "/wp-admin"}],
  "accept": ["text/html", "application/xhtml+xml", "application/xml", "text/xml"],
  "reference": "8b1f2c9e-…",
  "deadline_ms": 120000
}
```

| Field | Required | Meaning |
|---|---|---|
| `url` | yes | Absolute http(s) URL. |
| `resolve_bot_challenges` | no, default `false` | Permits local challenge resolution followed by paid fallback if available. Neither runs when false. |
| `exclusions` | no, default `[]` | URLs never to fetch: `[{"host", "path_prefix"}]`, at most 1,000. `host` is exact, `*.example.com` (the domain and every subdomain) or `*`; `path_prefix` (default `/`) matches whole decoded path segments, so `/admin` covers `/admin/users` but not `/administrator`. Checked on every redirect hop of the plain fetch and for every request the browser makes, on top of the service's own network policy. |
| `accept` | no, default any | Media types the caller stores. A successful response of any other type fails fast as `unsupported_media_type`, without its body. Error responses are still read, so a 429 or 404 fails with its own code. |
| `reference` | no | Caller's trace id (at most 256 characters), echoed back. Not used for deduplication. |
| `deadline_ms` | no | Upper bound for the whole capture. Defaults to the service's cap (120 s). |

Unknown fields and invalid values are rejected (400), and so is a `url` that the request's own exclusions cover.
There is deliberately no render mode or completion setting: both are automatic.

The service fetches and renders as one bot identity, `StolosioBot` (with an info URL, in a browser-shaped user
agent). Local resolution keeps the native browser identity; the paid tier keeps its provider's identity:
its stealth fingerprint is what gets it
through. A challenge tier that navigates on the provider's side (Browserless BrowserQL) applies the exclusions from
the handover on and to the page it landed on; redirect hops inside the provider's own navigation can't be checked.

## How the service gets there

```
plain HTTP ─► blocked / broken / unreachable ──────────────────────────────────────► failed (with the evidence)
    │
    ├─ a redirect hop is excluded ───────────────────────────────────────────────────► failed (excluded)
    │
    ├─ a type outside `accept` ──────────────────────────────────────────────────────► failed (unsupported_media_type)
    │
    ├─ another accepted type, or XML (never rendered) ───────────────────────────────► captured (response_body)
    │
    ├─ a body over the size cap (10 MiB) ─► managed browser (never returned as partial exact bytes)
    │
    ├─ looks usable ─► evidence that plain HTTP is enough here (method cache)? ──yes──► captured (response_body)
    │                    │ no (or a canary share)
    │                    ▼
    ├─ content missing / interstitial ─► managed browser ─► compare with the plain response
    │                                        │                ├─ plain had it all ─► captured (response_body, verified)
    │                                        │                └─ browser added content ─► captured (rendered_html)
    │                                        └─ bot challenge ─┐
    └─ bot challenge ──────────────────────────────────────────┴─► local resolution ─► paid resolution
       or block page                                                 (both only if resolve_bot_challenges)
```

- **Render by default.** Raw HTML can't show everything a browser adds (on a random sample of the web, rendering
  added real content to ~29% of pages, and a raw-HTML classifier found fewer than half of them). So every HTML page
  is rendered unless there is evidence that plain HTTP is enough for it.
- **Compare, and remember.** After a trustworthy render, the service measures how much of the rendered content the
  plain response already had (text shingles; text of known ad networks excluded). At ≥95% the plain response is
  **verified** and returned as the exact bytes; otherwise the rendered DOM is returned. The result is recorded in a
  method cache for the exact URL and for its URL pattern (ids and slugs generalised).
- **Skip only on evidence.** A capture skips rendering when the exact URL was HTTP-sufficient last time, or its URL
  pattern has ≥3 sufficient comparisons and hasn't been contradicted twice, and the plain response looks as usual
  (same size range, usable). Entries expire after 30 days without being seen. A 5% canary share of cache-approved
  captures is rendered anyway, so the evidence keeps refreshing itself.
- Bot protection tries **local resolution** when `resolve_bot_challenges` is true and a local tier is configured: a bounded native-browser retry
  with no external solver fee. The host supplies its own local tier; the default CDP implementation waits for
  browser-executable challenges. Images, fonts and media are permitted under the transfer cap;
  service workers remain blocked to preserve exclusions. Local fleet time still counts as cost.
- If protection holds, **paid challenge resolution** is tried only when `resolve_bot_challenges` is true.
  A block page refusing the IP/fingerprint uses paid fallback only when that tier is proxied.
- Each attempt is independently assessed; a solver result alone never means `captured`. Evidence distinguishes
  `local_resolution` from paid `challenge_resolution`, and `resolution_attempted` includes either tier.
- Resolution tiers do not repeat. Local retries have a 35-second budget with an initial 10-second challenge wait,
  extended up to 25 seconds only when challenge progress changes,
  bounded by the remaining capture deadline; failed local acquisition can still fall back to paid resolution.

- **Preserve evidence without claiming success.** Passing raw-HTML classification does not establish that
  JavaScript-loaded content is present. When required browser verification meets a challenge, gets an error
  status (403, 5xx, 429), fails, or renders nothing, the capture fails and retains the plain response as evidence.
  A refused or empty verification render yields transient `incomplete_content`; unavailable capacity, browser
  outages, deadlines and bot protection retain their specific failure codes. Uncached HTML without a configured
  browser fails as `browser_unavailable`. Cache-approved HTTP captures still skip rendering, but a failed canary
  verification is a failed capture and does not add HTTP-sufficiency evidence.
- Rendered documents include open shadow roots (web components) as declarative shadow DOM
  (`<template shadowrootmode="open">`), which browsers and HTML parsers read back.

## Response

Always the same JSON shape. HTTP **200** for every attempt result, failures included; **400** for an invalid
request; **503** only when the service can't accept requests at all (same body shape, `Retry-After` header).

```jsonc
{
  "schema": "2",
  "reference": "8b1f2c9e-…",
  "outcome": "captured",                               // "captured" | "failed"
  "requested_url": "https://jp.mercari.com/en/search?category_id=97",
  "final_url": "https://jp.mercari.com/en/search?category_id=97",
  "started_at": "2026-09-29T09:12:03.120Z",
  "finished_at": "2026-09-29T09:12:27.201Z",

  "response": {                                        // the response the document came from
    "status_code": 200,
    "headers": [["content-type", "text/html; charset=utf-8"], ["set-cookie", "a=1"], ["set-cookie", "b=2"]],
    "redirects": [{"status": 301, "url": "http://…", "location": "https://…"}]
  },

  "document": {                                        // on captured; on failed when there is evidence
    "representation": "rendered_html",                 // "response_body" | "rendered_html"
    "media_type": "text/html",
    "charset": "utf-8",
    "body_base64": "PCFET0NUWVBFIGh0bWw+…",
    "content_sha256": "3a7b…",
    "content_bytes": 766410
  },

  "failure": null,

  "evidence": {
    "attempts": [
      {
        "path": "http", "tier": "direct", "status_code": 200, "duration_ms": 612.0,
        "assessment": {"primary": "app_shell", "completeness": "empty", "confidence": 0.9,
                       "reasons": [{"code": "app_shell", "confidence": 0.9, "source": "rule"}]},
        "decision": "escalate", "reason_code": "assessment", "decision_reason": "app_shell: content missing without a browser",
        "steps": [], "notes": []
      },
      {
        "path": "browser", "tier": "managed", "status_code": 200, "duration_ms": 23600.0,
        "assessment": {"primary": null, "completeness": "complete", "confidence": 0.9, "reasons": []},
        "decision": "accept", "reason_code": "content_comparison", "decision_reason": "rendered content is complete (plain response had 12% of it)",
        "comparison": {"http_coverage": 0.12, "http_sufficient": false},
        "steps": [{"step": "parsed", "t": 0.9, "new_lines": 12, "new_items": 0},
                  {"step": "scroll 1", "t": 2.1, "new_lines": 3, "new_items": 20}],
        "notes": ["page drops content while scrolling: final HTML holds less than was shown"]
      }
    ],
    "cost": {"browser_seconds": 23.6, "paid": false, "bytes": 2874983},
    "versions": {"pagecapture": "0.1.0", "classifier": "rules", "renderer": "adaptive-5"}
  }
}
```

### `document`

- `response_body` — the exact bytes the server sent: HTML, or any other type in `accept` (XML, …), which is
  `captured` as sent. A response that declares no media type is sniffed (HTML, XML, else opaque). On a failure, an
  error page whose type is outside `accept` is left out.
- `rendered_html` — the browser's final DOM, serialized.
- Pages that drop content while scrolling (virtualized lists) are still `captured` — the content was seen — but
  the final DOM may hold less than was shown; the browser attempt's `notes` say so. A later schema version may add
  a second document with the text seen during rendering.

### `failure`

```jsonc
{
  "code": "rate_limited",
  "category": "website",            // "website" | "network" | "gateway" | "content"
  "transient": true,
  "message": "HTTP 429",
  "retry_after_seconds": 120,       // from Retry-After when sent (transient failures only; a refusing browser provider's default for capacity), else null
  "resolution_attempted": null      // for bot_challenge / bot_blocked: was challenge resolution tried?
}
```

| Code | Category | Transient | Meaning |
|---|---|---|---|
| `rate_limited` | website | yes | 429 or "too many requests" |
| `website_error` | website | yes | 5xx, maintenance page |
| `not_found` | website | no | 404, or a soft 404 (not-found page, deep link redirected to a front page) |
| `gone` | website | no | 410, or the page says it was removed |
| `access_denied` | website | no | 401/403, login wall |
| `request_rejected` | website | no | any other 4xx (400, 405, 414, …) |
| `paywall` | website | no | 402, content behind payment |
| `parked` | website | no | parked or for-sale domain, empty server default |
| `geo_blocked` | website | no | 451, or "not available in your region" |
| `bot_challenge` | website | no | a bot challenge that was not passed by available permitted tiers (`resolution_attempted`) |
| `bot_blocked` | website | no | a block page refusing this client or IP; local resolution then permitted proxied paid fallback (`resolution_attempted`) |
| `redirect_loop` | website | no | the redirects don't end: a loop, or more than 10 hops |
| `unreachable` | network | yes | no HTTP response: connection, TLS, timeout, a DNS failure not confirmed as a missing host |
| `host_not_found` | network | no | the host (the URL's or a redirect's) doesn't exist: a resolver confirmed it has no such name (NXDOMAIN) or no address |
| `capacity` | gateway | yes | no browser capacity right now, including a provider refusing for its plan's limits (BrowserQL 429/402 or a concurrency/rate/quota error; `retry_after_seconds` from Retry-After, else 30 s for concurrency and 3600 s for quota) |
| `browser_unavailable` | gateway | yes | a browser was needed but couldn't be used (a provider outage or fault) |
| `deadline_exceeded` | gateway | yes | the capture couldn't finish within the deadline |
| `incomplete_content` | content | yes | even after rendering the content isn't trustworthy (or the body is empty) |
| `interstitial` | content | yes | consent wall, queue or picker that couldn't be passed |
| `unsupported_browser` | content | no | the site refuses the client |
| `excluded` | content | no | a redirect, or the browser's navigation, led to a URL the request excludes (or the service's network policy blocks) |
| `unsupported_media_type` | content | no | a successful response of a media type outside `accept`; no document |

Only the **website** category is evidence about a site's health (for per-domain pacing and backoff); network,
gateway and content failures are not.

### `evidence`

Every step the service took, in order: path (`http`, `browser`) and tier (`direct`, `managed`,
`local_resolution`, `challenge_resolution`), what the assessment found (reason codes and confidences from
`docs/labels.md`), and why it
escalated, accepted or failed (e.g. `cache: pattern news.example.com/news/{id} HTTP-sufficient (7/7)`, or
`canary: re-checking a cached HTTP-sufficient page`). Browser attempts carry `comparison` — how much of the rendered
content the plain response already had, and whether that counted as sufficient. Browser attempts carry the renderer's step log (what each wait and scroll added)
and notes. `cost` sums the capture: browser seconds, whether a costlier tier was used (`paid`), bytes.
`versions` identify the service, classifier and renderer that produced the result.

## Mapping to periplus

| periplus | capture response |
|---|---|
| requested / effective URL | `requested_url`, `final_url` |
| status, ordered response headers | `response.status_code`, `response.headers` |
| document representation, media type, charset, sha256, bytes, body | `document.*` |
| outcome, failure code and message, retry-after | `outcome`, `failure.*` |
| customer-facing failure categories | `failure.code` (adds `bot_challenge`, `bot_blocked`, `parked`, `paywall`, `incomplete_content`, …) |
| operator URL exclusions | `exclusions` |
| HTML and XML only | `accept`: `text/html`, `application/xhtml+xml`, `application/xml`, `text/xml` |
| challenge resolution allowed by the organisation's budget | `resolve_bot_challenges` |
| pacing ignores transport failures | pace only on `failure.category == "website"` |
| browser steps | `evidence.attempts[].steps` |
| (new) HTTP vs browser, tier, cost | `evidence.attempts`, `evidence.cost` |

## Host integration

A host provides two adapters (`src/pagecapture/adapters.py`) and, optionally, the method cache's storage
(`src/pagecapture/cache.py`, `MethodCache`: async get and atomic record of comparison evidence for exact/pattern keys — stolosio would keep it next to its
per-domain evidence in Postgres; the default is in memory, or SQLite via `PAGECAPTURE_METHOD_CACHE`):

- a `Fetcher` — plain HTTP through the host's egress (proxy, network policy), returning an `HttpResponse` with
  ordered headers and redirects; raising `FetchError` when there is no response, `ExcludedUrl` when a redirect hop
  is excluded, and `UnsupportedMediaType` before reading a successful body of a type outside `accept`.
  `HttpxFetcher` does all of this (with an optional egress `proxy`); a host rejects its egress's own error
  answers by overriding `check_hop(response)`, and adds its own network policy to the request's exclusions;
- `BrowserTier`s — `managed`, optionally `local_resolution`, and optionally paid `challenge_resolution` —
  each rendering a URL with the adaptive
  renderer on its own browsers, given the request's exclusions (`CdpBrowserTier` wraps any CDP endpoint).

Defaults use `HttpxFetcher`, the CDP endpoint in `PAGECAPTURE_BROWSER_WS`, and for challenge resolution
`PAGECAPTURE_CHALLENGE_BROWSER_WS`: a Browserless BrowserQL URL (unblock server-side, then render over CDP) or any
CDP endpoint.

### Method-cache identity and concurrency

Exact keys preserve scheme, hostname (including `www`), effective port, path and raw query order/encoding.
Default and explicit default ports share an origin; different origins never share either exact or pattern evidence.
Pattern keys intentionally generalize article/product path segments and retain only encoded query names.
Hosts record both keys atomically. Contradictions are retained until the entry expires; later successful
comparisons cannot erase them. A contradicted exact URL cannot borrow pattern evidence to skip rendering.
Old development cache keys are invalidated by the new origin-bearing identity; clear the cache when updating.

### Structured acquisition reasons

Each attempt carries `reason_code`: `cache_url`, `cache_pattern`, `canary`, `verify_http`,
`assessment`, `content_comparison`, `acquisition`, or `media_type`. This is the stable machine-readable
reason for the acquisition decision. `decision_reason` is explanatory text; hosts must not parse it
for accounting or observations. Stolosio copies the code into its redacted completion event.
