---
title: Run your first session
description: Start Stolosio locally with Docker Compose and connect an existing Playwright client.
---

## Prerequisites

Install Docker with Docker Compose, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Use Python 3.13 or newer. Docker must be running.

No paid credentials are needed for the local HTTP and Browserless paths.

## 1. Start the stack

```bash
git clone https://github.com/elei-io/stolosio.git
cd stolosio
uv sync --locked
docker compose up --build -d
```

The Compose configuration publishes services on localhost. Its database credentials are for development only.

## 2. Start the fleet controller

In a second terminal, from the same repository:

```bash
uv run python -m backend.fleet.controllers.docker
```

Keep this process running. The API and fleet controller are separate processes: the controller reconciles Browserless workers and their session slots through Docker Compose.

## 3. Open the admin interface

Visit [localhost:5173](http://localhost:5173). The API listens on `http://localhost:8411`; the Docker fleet controller exposes metrics at `http://localhost:9101/metrics`.

Inspect fleet policy, provider availability, and sessions in the admin interface. Fleet limits and routing policy are saved in PostgreSQL, not configured through session URLs.

## 4. Run the example client

From the repository root:

```bash
uv run python examples/01_goto_and_content.py
```

This exercises navigation and HTML retrieval. A successful run returns page content without a protocol error. Inspect the resulting session in the admin interface.

To exercise browser-required commands:

```bash
uv run python examples/02_interaction.py
uv run python examples/03_evaluate.py
```

## 5. Connect your own automation

For an existing Playwright client, use:

```python
browser = await playwright.chromium.connect_over_cdp(
    "ws://localhost:8411/v1/connect"
)
```

Continue with the [complete Python example](/docs/clients/).

## Configuration and shutdown

The local stack works without an `.env` file. Use the repository's [`.env.example`](https://github.com/elei-io/stolosio/blob/main/.env.example) for supported overrides. For example, `STOLOSIO_ADMIN_PORT` changes the published admin port.

Stop the fleet controller with Ctrl+C, then stop the Compose stack:

```bash
docker compose down
```

This command does not request volume deletion. Follow [maintenance guidance](/docs/maintenance/) before resetting development data.

## If something fails

```bash
docker compose ps
docker compose logs --tail=100
```

Check that dependencies are healthy, the controller is running, and the expected localhost ports are available. See [troubleshooting](/docs/observability/).
