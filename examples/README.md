# Downstream client examples

These programs represent clients using Harbor through Playwright's existing CDP API.
They are executable examples now and end-to-end acceptance targets for Harbor as the
proxy is implemented.

Set `HARBOR_CDP_URL` to the Harbor WebSocket endpoint. For an explicit local browser
route:

```bash
export HARBOR_CDP_URL='ws://localhost:8411/v1/connect?harbor.provider.slug=browserless'
```

Run each example from the repository root:

```bash
uv run python examples/01_goto_and_content.py
uv run python examples/02_interaction.py
uv run python examples/03_evaluate.py
uv run python examples/04_debug_stream.py
uv run python examples/05_no_browser_http_only.py
uv run python examples/06_provider_transition.py
uv run python examples/07_transition_replay.py
uv run python examples/08_automatic_routing.py
```

The examples intentionally use only the standard Playwright client. They contain no
Harbor-specific SDK code; switching between Harbor routes requires changing only
`HARBOR_CDP_URL`. Omitting `harbor.provider.slug` selects Harbor's automatic plan.

## Targets

- `01_goto_and_content.py` covers the initial no-browser-eligible command sequence.
- `02_interaction.py` covers a command sequence that requires a browser provider.
- `03_evaluate.py` covers JavaScript evaluation through the CDP connection.
- `04_debug_stream.py` shows an opt-in client reference and the separate, read-only
  DEBUG WebSocket alongside an ordinary Playwright CDP connection.

## No-browser validation

The [adaptive transition milestone](../docs/roadmap/provider-transitions.md) includes
three self-checking examples:

- `05_no_browser_http_only.py` completes `goto` plus `content` on HTTP.
- `06_provider_transition.py` observes an automatic session moving between two
  providers and verifies the factual transition event.
- `07_transition_replay.py` replays two acknowledged navigations in order and catches
  up before the triggering evaluation runs.

Example 05 explicitly selects HTTP. Examples 06 and 07 use automatic routing and
require prepared support evidence whose cheapest provider cannot satisfy the later
evaluation; set `HARBOR_E2E_TRANSITIONS=1` to include them in E2E runs.

Examples 05 and 06 also observe the public DEBUG WebSocket to assert the factual
attempt sequence. Set `HARBOR_DEBUG_URL` when it isn't available at
`ws://localhost:8411/v1/debug`.

The Docker E2E suite will run those same example files, so a command that succeeds for
a developer is the exact downstream workflow exercised by automated acceptance.

`08_automatic_routing.py` omits the provider override and prints the factual attempt and
transition journey selected from Harbor's domain support plan.
