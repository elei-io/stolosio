# No-Browser Promotion

Status: implemented (initial Playwright 1.55 slice)

This milestone proves that Harbor can avoid acquiring a browser for a navigation and
content-only Playwright session, while retaining the same provider-neutral CDP URL and
promoting the live logical session to Chromium as soon as real browser behavior is
required.

The durable product rule is defined in [No-Browser Execution](../NO_BROWSER.md). This
milestone intentionally implements only navigation and full-page content retrieval.
Static selectors, title access, text extraction, and other conveniences remain future
work.

## Public contract

The downstream program remains an ordinary CDP client:

```python
browser = await playwright.chromium.connect_over_cdp(
    "ws://localhost:8411/v1/connect"
)
page = await browser.new_page()
await page.goto("https://example.com")
html = await page.content()
```

There is no Harbor SDK, promotion command, session token, or provider-specific route.
The downstream WebSocket and Harbor logical session identity survive promotion.

An explicit browser selection such as `harbor.provider.slug=chromium` bypasses the
HTTP optimization. Automatic selection uses domain history; explicit
`harbor.provider.slug=http` forces an HTTP start but still promotes when browser work
is observed, which keeps validation deterministic without changing CDP behavior.

## Protocol evidence

Diagnostics were run on 2026-07-17 with Playwright 1.55.0 against Plain Chromium
through Harbor's real `/v1/connect` WebSocket. Harbor's normalized DEBUG observations
captured method order, and a temporary local WebSocket tap captured parameter keys and
one-way hashes without storing page content, response bodies, or generated Playwright
source.

Playwright connection bootstrap sent:

```text
Browser.getVersion
Target.setAutoAttach
Browser.setDownloadBehavior
Target.getTargetInfo
```

Creating the first page brought the sequence to 22 commands:

```text
Browser.getVersion
Target.setAutoAttach
Browser.setDownloadBehavior
Target.getTargetInfo
Target.createBrowserContext
Browser.setDownloadBehavior
Target.createTarget
Browser.getWindowForTarget
Page.enable
Page.getFrameTree
Log.enable
Page.setLifecycleEventsEnabled
Runtime.enable
Page.addScriptToEvaluateOnNewDocument
Network.enable
Target.setAutoAttach
Emulation.setFocusEmulationEnabled
Browser.setWindowBounds
Emulation.setDeviceMetricsOverride
Emulation.setEmulatedMedia
Runtime.runIfWaitingForDebugger
Page.createIsolatedWorld
```

`page.goto()` then added exactly:

```text
Page.navigate
```

Observed commands after navigation were:

| Playwright operation | Additional CDP methods | Diagnostic conclusion |
| --- | --- | --- |
| `page.content()` | `Runtime.evaluate`, `Runtime.callFunctionOn` | Potential HTTP-safe operation only when the complete tested argument shape matches. |
| `page.title()` | `Runtime.evaluate`, `Runtime.callFunctionOn` | Same method shape as content; arguments differ. Must promote initially. |
| `page.evaluate(...)` | `Runtime.evaluate`, `Runtime.callFunctionOn` | Same method shape as content; user expression is carried in call arguments. Must promote. |
| `locator("h1").text_content()` | two `Runtime.evaluate` calls, then `Runtime.callFunctionOn` | Injects the large locator runtime. Must promote. |
| `locator("a").click()` | Runtime and DOM lookup calls followed by three `Input.dispatchMouseEvent` calls | Clearly interactive. Must promote before the sequence executes. |
| `page.mouse.wheel(...)` | `Input.dispatchMouseEvent` | Clearly interactive. Must promote. |
| `page.screenshot()` | Runtime setup followed by `Page.getLayoutMetrics` | Must promote; the current capability baseline rejects `Page.getLayoutMetrics`. |

The content, title, and user evaluation traces prove that method names are not enough
to reconstruct Playwright API calls. The current content and title flows even share
the same `Runtime.evaluate` expression and `Runtime.callFunctionOn` function hashes;
only their serialized arguments distinguish them.

For this pinned version, the shared Playwright utility expression was 10,309 bytes with
SHA-256 prefix `6947ff8ef8a7`; the shared call function was 59 bytes with prefix
`e39fcbf038cf`. The serialized call arguments differed: content was 396 bytes with
prefix `9ce456a3fc44`, title was 144 bytes with prefix `fb4428cc81a1`, and the tested user
evaluation was 146 bytes with prefix `de180574a301`. These hashes are diagnostic
fingerprints, not a permanent public contract.

Consequences for the implementation:

- `Runtime.evaluate` cannot be classified as interaction by itself.
- `Runtime.callFunctionOn` cannot be classified as content retrieval by itself.
- Harbor may recognize only a complete, versioned, acceptance-tested content request.
- Any unknown, malformed, or changed signature promotes before the pending operation
  executes.
- A signature mismatch costs a browser acquisition but never risks returning invented
  or incomplete data.

The diagnostic also found two current compatibility facts outside this milestone:
Chromium tolerated two failed `Runtime.releaseObject` cleanup calls during a successful
click, while the unsupported `Page.getLayoutMetrics` command caused Playwright's
screenshot path to close its driver connection. These belong in later CDP conformance
work and are not reasons to expand the no-browser scope.

## Execution model

Harbor cannot choose HTTP at connection time because the target domain first appears
in `Page.navigate`. It also cannot acquire Chromium before accepting CDP, because that
would spend the resource the milestone exists to avoid.

An eligible automatic session therefore starts in a lazy CDP facade:

```text
logical session admitted
        |
        v
lazy CDP bootstrap
        |
        v
Page.navigate reveals domain
        |
        +--> eligible domain --> HTTP attempt --> synthetic CDP observations
        |
        +--> browser-required domain --> Chromium attempt --> real navigation

HTTP mode -- first non-content operation --> Chromium queue --> replay --> continue
```

The facade is a stateful protocol endpoint, not an HTTP response masquerading as a
WebSocket. It answers only the tested CDP bootstrap required to create one default page,
maintains Harbor-owned identifiers, and either supplies the bounded HTTP behavior or
maps those identifiers onto a real Chromium session after promotion.

Every command acknowledged before promotion is appended to an ordered, session-local
replay log. Promotion is not complete merely when Chromium reaches the latest URL. The
real browser must replay and catch up through the entire acknowledged command history
before Harbor executes the command that triggered promotion.

## Initial state machine

```text
lazy -> http_navigating -> http_ready -> closed
  |           |                |
  +-----------+----------------+-> promoting -> browser -> closed
                                      |
                                      +-> failed
```

- `lazy`: no provider resource has been acquired. Harbor accepts only tested bootstrap
  commands until it sees the first navigation or a command requiring promotion.
- `http_navigating`: a durable HTTP acquisition attempt owns the request.
- `http_ready`: Harbor retains the final URL, status, selected headers, and response
  HTML needed for the tested content response.
- `promoting`: Harbor queues for Chromium, creates the corresponding context and page,
  replays every previously acknowledged command in original order, waits for each
  command to settle, and only then releases the pending browser-required command.
- `browser`: subsequent CDP traffic uses Chromium through Harbor's identifier mapping.

Only one context, one target, and one main frame are supported in HTTP mode. A second
context or target, non-default process or page configuration, or any unrecognized
bootstrap parameter promotes immediately.

## HTTP behavior

The first HTTP attempt uses Harbor's async HTTP client and follows ordinary redirects.
It records the requested URL, final sanitized URL, status, selected safe headers,
timing, transport failure, and response size. Bodies remain session-local and never
enter DEBUG, NATS, Prometheus labels, or analytical projections.

For the initial Playwright acceptance target, Harbor synthesizes only the responses and
events needed for:

- The tested connection and first-page bootstrap.
- `Page.navigate` for the main frame.
- Main-document request, response, redirect, DOM-ready, and load observations.
- The exact tested `page.content()` request for the pinned Playwright version.

Harbor returns the fetched HTML for content retrieval. It does not execute JavaScript,
apply a DOM parser, answer selectors, infer a title, or claim browser rendering. Empty
or JavaScript-only HTML is still reported truthfully; automatic correctness judgments
remain later analytical work.

## Promotion behavior

Promotion is triggered by the first command sequence outside the tested bootstrap,
navigation, and content recognizers. Harbor must promote before responding to or
executing that operation.

Promotion performs these steps:

1. Record a factual promotion trigger for the current domain and logical session.
2. Complete the HTTP attempt with the stable `promoted` outcome.
3. Enter the ordinary Chromium provider FIFO without releasing the global session.
4. Acquire a ready Chromium instance slot through existing fleet placement.
5. Create a real browser context, target, attached CDP session, frame, and execution
   worlds.
6. Map the facade's context, target, session, frame, loader, and execution identifiers
   to their Chromium equivalents.
7. Replay every previously acknowledged session command in its original order,
   translating Harbor-owned identifiers to Chromium identifiers as they are created.
8. Wait for each replayed response and required lifecycle boundary so Chromium is fully
   caught up. This includes every prior navigation, not only the latest URL, and prior
   content retrieval commands even when they do not mutate page state.
9. Suppress replay responses and replay-generated duplicate events because the
   downstream client already observed the corresponding HTTP-facade responses and
   events.
10. Dispatch the still-pending browser-required command only after catch-up completes,
    then continue as a browser session.

If acquisition, replay, or navigation fails, Harbor reports the real failure, releases
both attempts idempotently, and closes the logical session. It never falls back to a
fabricated success.

### Replay log

The replay log contains the structured downstream CDP commands Harbor actually
acknowledged, including bootstrap, target setup, emulation defaults, navigation, and
content retrieval. It preserves command order and the identifier relationships needed
for translation. The promotion-triggering command is held separately and is not part
of catch-up.

The log is bounded by the logical session lifetime and explicit command-count and byte
budgets. Reaching either budget triggers promotion before Harbor acknowledges the
command that would exceed it, allowing the bounded history to be replayed first. The
log remains in the API process that owns the downstream WebSocket. It may contain
command parameters needed for correct replay, so it is never published to DEBUG or
NATS, stored in analytical projections, or exposed as metric labels. A replica failure
already terminates the live socket and does not require durable replay recovery.

Replay is all-or-nothing from the client's perspective. If any acknowledged command
cannot be translated, returns an incompatible result, times out, or causes Chromium to
disconnect, Harbor fails promotion and the pending command. It must not skip the
command, continue from the latest URL, or expose a partially caught-up browser.

## Historical rule

This milestone originally used the deliberately small rule below:

- An unseen domain may begin through HTTP.
- A domain with no historical promotion-triggering operation may begin through HTTP.
- Once a session on a domain promotes because of a browser-required operation, future
  automatic sessions for that domain acquire Chromium before executing navigation.

Existing raw `domain_command_stats` cannot directly answer this question because
content, title, and arbitrary evaluation share CDP method names. The milestone adds a
factual, durable per-domain promotion projection populated from successful protocol
classification. It records counts and the stable triggering method class, not a score,
recommendation, or permanent conclusion.

The implemented [deterministic routing milestone](deterministic-routing.md) supersedes
that sticky rule. Unknown domains now use the operator default, while HTTP must qualify
through strict background comparison. The promotion projection remains factual evidence.

## Attempts, observations, and metrics

HTTP is recorded as an acquisition provider even though it has no browser instance.
One logical session may therefore contain:

```text
attempt 1: http -> completed(promoted)
attempt 2: chromium -> active -> completed
```

HTTP navigation emits the same normalized factual navigation observations as a browser
where equivalent evidence exists. Promotion adds a factual lifecycle observation that
states what changed and which stable command class triggered it. It does not say that
promotion was recommended or that the original provider was bad.

Prometheus exposes HTTP active, queued, and capacity gauges through the ordinary
provider-attempt metrics, acquisition outcomes through the existing counters, and a
bounded promotion counter labelled by source, target, and stable trigger class.

Domains, URLs, command arguments, expression hashes, and session identifiers never
become metric labels.

## Implementation slices

1. Check in protocol-conformance tests that capture the tested bootstrap, navigation,
   content, and interactive boundaries for the pinned Playwright version.
2. Refactor the gateway so an admitted logical session can accept CDP before owning a
   provider attempt.
3. Implement the one-context, one-page lazy facade and HTTP navigation events.
4. Implement the exact content recognizer and response without adding selectors or
   other static operations.
5. Implement Chromium promotion, identifier mapping, ordered full-history replay,
   duplicate suppression, and pending-command continuation.
6. Persist factual promotion history and use it for the initial domain rule.
7. Add DEBUG observations, bounded metrics, runnable validation examples, failure
   tests, and Docker E2E acceptance.

Each slice must leave unknown behavior on the safe path: acquire a real browser or fail
explicitly.

## Runnable validation examples

The milestone adds three self-checking programs under `examples/`:

```text
05_no_browser_http_only.py
06_no_browser_promotion.py
07_no_browser_history.py
```

- `05_no_browser_http_only.py` disables JavaScript, then performs `goto + content`
  through an explicitly selected HTTP start and asserts DEBUG contains only the HTTP
  attempt.
- `06_no_browser_promotion.py` starts through HTTP and verifies that a triggering
  evaluation observes the replayed Chromium document plus the factual HTTP-to-Chromium
  attempt sequence and promotion event.
- `07_no_browser_history.py` performs two navigations and two content reads through
  HTTP, then verifies that the triggering evaluation sees the fully replayed state and
  browser history.

The programs use standard Playwright plus Harbor's public read-only DEBUG WebSocket.
They do not import backend modules, query PostgreSQL, run Docker commands, or use a
Harbor SDK. Endpoints are configurable through `HARBOR_CDP_URL` and
`HARBOR_DEBUG_URL`. Each program prints a short result and exits non-zero when the
functional result or observed execution path is wrong.

The Docker E2E suite invokes all three programs and separately exercises full replay of
both navigations and content calls before the triggering command.

## Acceptance tests

The examples and Docker E2E suite cover the implemented boundary:

1. Disabling JavaScript followed by `goto + content` completes through an HTTP attempt
   without acquiring Chromium.
2. Explicit Chromium selection continues to use Chromium immediately.
3. Click and evaluation workflows starting through HTTP promote and complete against a
   real browser.
4. A later automatic session for a domain with promotion history acquires Chromium at
   navigation rather than performing HTTP first.
5. Promotion after multiple navigations and content reads replays every acknowledged
   command in order, suppresses replay output, and executes the triggering evaluation
   only after Chromium is fully caught up.
6. Attempts record `http -> completed(promoted) -> chromium`, and the normalized
   `execution.promoted` observation records the factual transition.
7. No raw HTML, Runtime arguments, query values, or protocol signatures enter the
   promotion observation, durable domain projection, or metric labels.

## Explicitly deferred

- Static selectors, title extraction, attributes, and other parser-backed commands.
- JavaScript execution in HTTP mode.
- Multiple pages or browser contexts before promotion.
- Promotion to Browserless, Lightpanda, or Camoufox.
- HTTP correctness scoring, empty-shell detection, confidence, and policy decay.
- Proxy selection, stealth selection, retries across providers, and cost optimization.
- Support for untested Playwright, Puppeteer, or raw-CDP content signatures.

## Exit condition

The milestone is complete when an unchanged Playwright `goto + content` workflow can
finish through HTTP without consuming a browser slot, while an unchanged interactive
workflow promotes the same logical CDP session to Chromium and completes correctly,
with durable attempts, truthful DEBUG evidence, bounded metrics, and no client knowledge
of the transition.
