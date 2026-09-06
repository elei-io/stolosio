# Contributing

Stolosio is experimental. Small bug fixes, reproducible reports, and documentation
improvements are welcome. Discuss larger changes in an issue before implementing them.

Follow the [README](README.md) for setup and [AGENTS.md](AGENTS.md) for implementation
conventions. Read the relevant architecture document before changing a public contract.

Before submitting a change:

```bash
uv sync --locked --dev
uv run ruff check .
uv run pytest -m "not e2e"
```

PostgreSQL and NATS must be running for integration tests; otherwise some tests skip.
For browser changes, also run `STOLOSIO_E2E=1 uv run pytest -m e2e` with the full
Compose stack and fleet controller running. Browserbase checks require credentials
and may incur charges; the local provider tests do not require a paid account.

For frontend changes, run from `web/`:

```bash
npm ci
npm run lint
npm run typecheck
npm run build
```

Describe the problem, the resulting behavior, and the checks you ran in your pull
request. Add a focused regression test for behavior changes and update affected docs.
Never include credentials, private page content, or customer data in issues or tests.
