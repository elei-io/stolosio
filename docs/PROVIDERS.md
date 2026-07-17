# Browser Providers

Harbor initially supports four browser providers plus the no-browser HTTP path. They do
not offer identical engines or protocols. This document records known differences that
matter when implementing Harbor's common downstream contract.

The matrix separates documented provider behavior from behavior verified by Harbor's
own end-to-end examples. It should be updated as compatibility tests grow.

Provider routing profiles assign operator-configurable cost units per session-second.
These are relative policy values rather than universal pricing claims. Historical
attempt cost supersedes that fallback after evidence exists. Qualification exercises
the full adaptive Harbor path, so direct-provider success alone cannot make a provider
eligible for automatic routing.

## Summary matrix

| Provider | Engine | Native control | Rendering | Primary advantage | Primary limitation |
| --- | --- | --- | --- | --- | --- |
| HTTP | Async HTTP client | Harbor-emulated CDP subset | No | Lowest possible cost | Initially limited to navigation and content retrieval |
| Plain Chromium | Chrome Headless Shell | CDP | Yes | Reference CDP behavior without another service layer | Resource-heavy and no built-in queue or session manager |
| Browserless Chromium | Chromium managed by Browserless | CDP plus Browserless lifecycle | Yes | Built-in concurrency, queueing, timeouts, and crash isolation | Adds provider behavior and has licensing implications |
| Lightpanda | Custom Zig browser with V8 | CDP subset | No graphical renderer | Very low startup and resource cost | Incomplete Web Platform and CDP coverage |
| Camoufox | Modified Firefox | Playwright Firefox/Juggler | Yes | Fingerprint rotation and anti-detection patches | Not CDP; remote serving and current releases are experimental |

## Capability matrix

`Yes` means the capability is expected from the provider. `Partial` means coverage is
provider-specific or incomplete. `No` means it is outside the provider's design. A
blank cell has not yet been established by Harbor tests.

| Capability | HTTP | Plain Chromium | Browserless | Lightpanda | Camoufox |
| --- | --- | --- | --- | --- | --- |
| Playwright `connect_over_cdp` | Yes, bounded facade | Yes | Yes | Yes, subset | No, requires Harbor mapping |
| JavaScript execution | No | Yes | Yes | Yes | Yes |
| DOM access | No; full HTML only | Yes | Yes | Yes, partial Web APIs | Yes |
| Click and form interaction | No | Yes | Yes | Yes, supported subset | Yes |
| Graphical layout | No | Yes | Yes | No | Yes |
| Screenshots | No | Yes | Yes | No | Yes |
| PDF generation | No | Yes | Yes | No | Provider/Firefox dependent |
| Multiple browser contexts | No | Yes | Yes | No, currently one | Playwright-native; remote server is one browser instance |
| Multiple page targets | No | Yes | Yes | No, currently one | Yes within the browser instance |
| Request interception | Native HTTP request only | Yes | Yes | Supported subset | Yes through Playwright |
| Browser fingerprint rotation | No | Default browser identity | Default browser identity unless separately configured | Not its primary purpose | Yes, per browser instance |
| Built-in queueing | No | No | Yes | No | No |
| Built-in session lifecycle | No | No | Yes | No | Experimental remote server |
| Native Harbor transport today | Yes, bounded facade | Yes | Yes | Yes | Mapped subset |

## Locally verified common behavior

On 2026-07-16, the same unmodified Playwright clients connected through Harbor and
passed against Plain Chromium, Browserless Chromium, Lightpanda, and Harbor's bounded
Camoufox mapping for:

- Navigation followed by page content retrieval.
- Navigation followed by a link interaction.
- Navigation followed by JavaScript evaluation.

The tests also established a current Lightpanda boundary:

- Creating a second browser context returns `Cannot have more than one browser context
  at a time`.
- Creating a second page target returns `TargetAlreadyLoaded`.

These are provider observations, not assumptions Harbor should hide in the passthrough
layer. Compatibility mapping may address them later if real downstream usage requires
it.

## Provider notes

### HTTP

HTTP is treated as a provider selection even though no browser is acquired. Initially,
only `page.goto` and `page.content` remain on this path. Any other command triggers
browser acquisition and ordered replay of every acknowledged command as described in
[No-Browser Execution](NO_BROWSER.md).

### Plain Chromium

The local `chromedp/headless-shell` image runs Chrome Headless Shell. This is the
standalone form of Chrome's old headless implementation, not modern unified headless
Chrome. It provides the broadest direct CDP reference in Harbor's current local stack,
including graphical output, multiple targets, and multiple contexts.

Harbor is responsible for acquisition, queueing, isolation, timeouts, and cleanup when
using this provider.

### Browserless Chromium

Browserless wraps Chromium with concurrency control, request queueing, configurable
timeouts, crash isolation, and debugging features. Its CDP traffic can pass through
Harbor, but its connection lifecycle and queue state are provider-specific inputs to
Harbor's session and metrics abstractions.

Browserless is licensed under SSPL-1.0 or a commercial license. The upstream project
states that proprietary commercial or CI use requires a commercial license. This must
be resolved before treating it as a production provider.

### Lightpanda

Lightpanda is built from scratch for machine-driven browsing and exposes a CDP endpoint
for Playwright and Puppeteer. It executes JavaScript and supports DOM interaction, but
it intentionally has no graphical rendering engine. Screenshots, graphical layout, and
other rendering-dependent behavior cannot be assumed.

Lightpanda describes itself as beta software with growing Web API coverage. Harbor must
measure command compatibility rather than infer full support from the presence of a CDP
endpoint.

Harbor forwards `Target.closeTarget`, which Lightpanda supports for a valid target.
Lightpanda does not implement `Emulation.setScriptExecutionDisabled`; Harbor maps the
Playwright setup request with `value: false` to a no-op because Lightpanda always has
script execution enabled. A request with `value: true` still returns an explicit
provider error rather than pretending that scripts were disabled.

Lightpanda is a managed fleet provider with one slot per instance. Its assigned instance
endpoint is passed to the direct-CDP adapter, and the provider-neutral reconciler scales
instances from Lightpanda queue demand. Docker Compose is the first runtime driver;
future Kubernetes or k3s support does not require different Lightpanda admission or
scaling policy.

### Camoufox

Camoufox is a modified Firefox browser focused on anti-detection. It changes fingerprint
properties inside the browser implementation and uses Playwright's Firefox/Juggler
protocol rather than CDP.

Its remote server is explicitly experimental and uses undocumented Playwright methods.
The server hosts one browser instance, so fingerprints do not rotate merely because a
new client session connects; the browser instance must be rotated. Current 2026 releases
are also described upstream as highly experimental. Harbor currently maps only the
commands and events proven by its initial navigation, interaction, and evaluation
conformance cases. The mapping must grow one explicitly tested behavior at a time.

## Sources

- [Chrome Headless mode](https://developer.chrome.com/docs/chromium/headless)
- [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
- [Browserless repository and licensing](https://github.com/browserless/browserless)
- [Lightpanda documentation](https://lightpanda.io/docs/)
- [Lightpanda repository and implementation status](https://github.com/lightpanda-io/browser)
- [Camoufox introduction](https://camoufox.com/)
- [Camoufox remote server](https://camoufox.com/python/remote-server/)
- [Camoufox stealth design](https://camoufox.com/stealth/)
