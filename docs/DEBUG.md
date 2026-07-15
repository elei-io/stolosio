# Debug Stream

Harbor provides an opinionated, standardized stream of session observations.

The stream is initially an internal tool for understanding sessions and comparing
provider behavior. It may later be exposed to downstream clients as a Harbor feature.

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
- A promotion or provider change is required.
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

No event schema is defined yet. The schema should emerge from examining real provider
events and choosing the smallest useful subset rather than designing a speculative
format in advance.

Future interpretation of these observations is described in [Analytics](ANALYTICS.md).
