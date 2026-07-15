# Downstream client examples

These programs represent clients using Harbor through Playwright's existing CDP API.
They are executable examples now and end-to-end acceptance targets for Harbor as the
proxy is implemented.

Set `HARBOR_CDP_URL` to the Harbor WebSocket endpoint. It defaults to the planned local
Chromium route:

```bash
export HARBOR_CDP_URL=ws://localhost:8000/v1/connect/chromium
```

Run each example from the repository root:

```bash
uv run python examples/01_goto_and_content.py
uv run python examples/02_interaction.py
uv run python examples/03_evaluate.py
```

The examples intentionally use only the standard Playwright client. They contain no
Harbor-specific SDK code; switching between Harbor routes requires changing only
`HARBOR_CDP_URL`.

## Targets

- `01_goto_and_content.py` covers the initial no-browser-eligible command sequence.
- `02_interaction.py` covers a command sequence that must use or promote to a browser.
- `03_evaluate.py` covers JavaScript evaluation through the CDP connection.
