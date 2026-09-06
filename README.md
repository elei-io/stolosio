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

## Status

Experimental and intended for local development or trusted networks. There is no
application authentication: do not expose the browser or administrative endpoints to
the internet. See [Security](SECURITY.md) for the deployment boundary.

The current providers are HTTP, Browserless, and optional paid Browserbase. Automatic
sessions can escalate from HTTP to a browser when needed. Multi-tenant operation and
production compatibility guarantees are not currently supported.

## Design decisions

- PostgreSQL owns transactional admission, leases, and fleet policy so concurrent
  requests cannot independently claim the same capacity.
- NATS carries live coordination; a transactional outbox and JetStream deliver durable
  observations without putting messaging on the admission path.
- Fleet reconciliation runs separately from the API. A session consumes capacity,
  while acquisition attempts receive browser slots.
- Native CDP traffic preserves browser semantics. The HTTP path supports a deliberately
  small command set and escalates when it cannot execute a command correctly.

See [Architecture](docs/ARCHITECTURE.md) for the boundaries and tradeoffs.

## Developer setup

Requirements: Docker, Docker Compose, and
[`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/elei-io/stolosio.git
cd stolosio
uv sync --locked
docker compose up --build -d
```

No paid credentials are needed for the local HTTP and Browserless paths. `.env` is
optional; use [.env.example](.env.example) for supported overrides. Fleet limits and
routing policy are managed in the UI, not environment variables.

The Stolosio admin UI is available at `http://localhost:5173` by default. Set
`STOLOSIO_ADMIN_PORT` to publish it on a different host port.

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

The publishing workflow targets `ghcr.io/elei-io/stolosio` and
`ghcr.io/elei-io/stolosio-admin`; the chart is published as
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

## Contributing and licensing

See [Contributing](CONTRIBUTING.md) for checks and pull request expectations.
Stolosio's original code is licensed under the [MIT License](LICENSE), copyright
2026 elei.io. Dependencies retain their own licenses; see
[Third-party licensing](THIRD_PARTY_LICENSES.md), especially the Browserless terms.

The `admin/` package is the operator interface, published as `stolosio-admin`.
It is separate from `public-site/`, the static marketing/documentation package
published in the repository as `stolosio-public`.

To preview the public site locally, run `npm ci` and `npm run dev` from
`public-site/`, then open <http://127.0.0.1:4321>. See the
[public site guide](public-site/README.md) for build, validation, and production
preview instructions. The public site requires no running Stolosio services.
