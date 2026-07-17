# Harbor

Harbor is a browser and web-acquisition gateway. It gives CDP-compatible automation
clients one endpoint for sessions across Chromium, Browserless Chromium, Lightpanda,
and Camoufox.

It is for teams building browser automation, scraping, testing, and web-data systems
that want to change providers without rewriting downstream automation. Harbor owns
session admission, provider queues, managed browser fleets, observations, and
eventually cost-aware acquisition planning.

Harbor checks each provider independently per domain, orders supported providers by
cost, and transitions through that plan when a journey reveals a new requirement. An
operator-selected default remains the final automatic candidate.

## Developer setup

Requirements: Docker, Docker Compose, and
[`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
docker compose up --build -d
```

The Harbor UI is available at `http://localhost:5173` by default. Set
`HARBOR_WEB_PORT` to publish it on a different host port.

Run the development fleet controller in another terminal. It reconciles the managed
Chromium, Browserless, Lightpanda, and Camoufox fleets through Docker Compose:

```bash
uv run python -m backend.fleet.controllers.docker
```

The controller exposes its scaling and reconciliation metrics on
<http://localhost:9101/metrics> by default.

The API starts at <http://localhost:8411>. Connect Playwright through Harbor:

```python
browser = await playwright.chromium.connect_over_cdp(
    "ws://localhost:8411/v1/connect"
)
```

Run the examples and checks:

```bash
uv run python examples/01_goto_and_content.py
uv run python examples/02_interaction.py
uv run python examples/03_evaluate.py
uv run pytest
uv run ruff check .
```

The Compose stack is required for examples and E2E tests:

```bash
HARBOR_E2E=1 uv run pytest -m e2e
```

## Documentation

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Fleet management](docs/FLEET_MANAGEMENT.md)
- [Provider matrix](docs/PROVIDERS.md)
- [DEBUG stream](docs/DEBUG.md)
- [No-browser execution](docs/NO_BROWSER.md)
- [Deterministic domain routing](docs/ANALYTICS.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributor and agent guide](AGENTS.md)
