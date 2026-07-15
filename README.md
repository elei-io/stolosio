# Harbor

Harbor is a browser acquisition fleet with a unified session contract, queue metrics,
and browser-specific adapters. Its initial providers are Browserless Chromium, plain
Chromium, Lightpanda, and Camoufox.

## Local development

Install the Python environment and run the API directly:

```bash
uv sync
uv run uvicorn backend.api.main:app --reload
```

Or start the complete local stack:

```bash
docker compose up --build
```

The local endpoints are:

- API: <http://localhost:8000>
- OpenAPI documentation: <http://localhost:8000/docs>
- Browserless Chromium: `ws://localhost:3000`
- Camoufox Playwright/Firefox: `ws://localhost:1234/harbor`
- Plain Chromium CDP: `ws://localhost:9223`
- Lightpanda CDP: `ws://localhost:9222`

Copy `.env.example` to `.env` to override local settings. Compose supplies its own
service-network defaults, while the application defaults target host-local services.

Browser containers are development infrastructure only. Harbor addresses browsers via
configured endpoints so production orchestration can be delegated to Kubernetes or
another platform.

Browserless Chromium, Chromium, and Lightpanda expose CDP-compatible endpoints.
Camoufox is Firefox-based and exposes Playwright's Firefox/Juggler protocol instead;
clients connect with `playwright.firefox.connect(...)`. Its remote server support is
experimental upstream.
