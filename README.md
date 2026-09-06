# Stolosio

Stolosio is a browser and web-acquisition gateway. It gives CDP-compatible automation
clients one endpoint across a bounded HTTP path, a Stolosio-managed Browserless fleet,
and quota-controlled Browserbase capacity.

It is for teams building browser automation, scraping, testing, and web-data systems
that want to change providers without rewriting downstream automation. Stolosio owns
session admission, provider queues, managed browser fleets, observations, and
eventually cost-aware acquisition planning.

Native browser traffic is opaque CDP passthrough. Stolosio owns admission, capacity,
session lifecycle, observations, and routing; the selected browser remains the
authority on individual CDP methods.

## Developer setup

Requirements: Docker, Docker Compose, and
[`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
docker compose up --build -d
```

The Stolosio UI is available at `http://localhost:5173` by default. Set
`STOLOSIO_WEB_PORT` to publish it on a different host port.

Run the development fleet controller in another terminal. It reconciles Browserless
workers and their configured session slots through Docker Compose:

```bash
uv run python -m backend.fleet.controllers.docker
```

The controller exposes its scaling and reconciliation metrics on
<http://localhost:9101/metrics> by default.

The API starts at <http://localhost:8411>. Connect Playwright through Stolosio:

```python
browser = await playwright.chromium.connect_over_cdp(
    "ws://localhost:8411/v1/connect"
)
```

Browserbase is never used by automatic routing unless the session explicitly opts in:

```text
ws://localhost:8411/v1/connect?stolosio.provider.allow_paid_fallback=true
```

Even with that opt-in, Stolosio exhausts its local HTTP/Browserless plan before using
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
STOLOSIO_E2E=1 uv run pytest -m e2e
```

## Kubernetes and k3s

The Helm chart under `charts/stolosio` installs Stolosio, its Kubernetes fleet controller,
and the Stolosio-managed Browserless workload. It requires existing PostgreSQL and NATS
connection Secrets and creates separate API and UI Services; it does not provision
those dependencies, ingress, DNS, or TLS. See
[Kubernetes and k3s](docs/KUBERNETES.md).

Release images are published as `ghcr.io/elei-io/stolosio` and
`ghcr.io/elei-io/stolosio-web`; the chart is published as
`oci://ghcr.io/elei-io/charts/stolosio`. A ready-to-copy Flux example lives under
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
