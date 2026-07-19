# Harbor

Harbor is a browser and web-acquisition gateway. It gives CDP-compatible automation
clients one endpoint across a bounded HTTP path, a Harbor-managed Browserless fleet,
and quota-controlled Browserbase capacity.

It is for teams building browser automation, scraping, testing, and web-data systems
that want to change providers without rewriting downstream automation. Harbor owns
session admission, provider queues, managed browser fleets, observations, and
eventually cost-aware acquisition planning.

Native browser traffic is opaque CDP passthrough. Harbor owns admission, capacity,
session lifecycle, observations, and routing; the selected browser remains the
authority on individual CDP methods.

## Developer setup

Requirements: Docker, Docker Compose, and
[`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
docker compose up --build -d
```

The Harbor UI is available at `http://localhost:5173` by default. Set
`HARBOR_WEB_PORT` to publish it on a different host port.

Run the development fleet controller in another terminal. It reconciles Browserless
workers and their configured session slots through Docker Compose:

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

Browserbase is never used by automatic routing unless the session explicitly opts in:

```text
ws://localhost:8411/v1/connect?harbor.provider.allow_paid_fallback=true
```

Even with that opt-in, Harbor exhausts its local HTTP/Browserless plan before using
the paid fallback.

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

## Kubernetes and k3s

The Helm chart under `charts/harbor` installs Harbor, its Kubernetes fleet controller,
and the Harbor-managed Browserless workload. It requires existing PostgreSQL and NATS
connection Secrets and creates separate API and UI Services; it does not provision
those dependencies, ingress, DNS, or TLS. See
[Kubernetes and k3s](docs/KUBERNETES.md).

Release images are published as `ghcr.io/ekkuleivonen/harbor` and
`ghcr.io/ekkuleivonen/harbor-web`; the chart is published as
`oci://ghcr.io/ekkuleivonen/charts/harbor`. A ready-to-copy Flux example lives under
[`deploy/flux`](deploy/flux).

## Documentation

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Fleet management](docs/FLEET_MANAGEMENT.md)
- [Kubernetes and k3s](docs/KUBERNETES.md)
- [Provider matrix](docs/PROVIDERS.md)
- [DEBUG stream](docs/DEBUG.md)
- [No-browser execution](docs/NO_BROWSER.md)
- [Deterministic domain routing](docs/ANALYTICS.md)
- [Global domain blocking](docs/NETWORK_POLICY.md)
- [Roadmap](docs/ROADMAP.md)
- [CI and releases](docs/RELEASING.md)
- [Contributor and agent guide](AGENTS.md)
