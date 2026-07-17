# Working on Harbor

This file is the implementation guide for coding agents and contributors. Read the
relevant documents in `docs/` before changing a public contract or architectural
boundary.

## Product contract

Harbor presents one CDP-compatible endpoint to downstream automation:

```text
WS /v1/connect
```

- Existing CDP and Playwright `connect_over_cdp()` clients should need only a URL
  change.
- Harbor-owned connection settings use the `harbor.*` query namespace.
- Explicit query settings override Harbor's automatic plan, which overrides defaults.
- Do not expose provider addresses or add provider names to public route paths.
- Unsupported behavior must return an explicit protocol error. Never silently succeed,
  lose HTML, or substitute materially different behavior.

## Architectural boundaries

- `backend/api/` owns HTTP/WebSocket transport and application lifecycle only.
- `backend/proxy/` owns planning, settings resolution, admission, provider adapters,
  session lifecycle, and protocol transport.
- Provider adapters satisfy Harbor's internal contract; they do not define the public
  API.
- Native CDP providers should preserve command IDs, session IDs, event order,
  backpressure, and close behavior.
- Camoufox mappings must be implemented and tested command by command. Do not claim
  general CDP compatibility from a partial mapping.
- PostgreSQL is the durable source of truth and owns transactional admission, queues,
  leases, capacity, and analytical projections.
- Logical sessions consume global Harbor capacity and never own a permanent provider.
  Provider queues contain acquisition attempts, not sessions.
- Managed provider fleets contain browser instances, and instances expose session
  slots. Only healthy, ready, non-draining instances contribute provider capacity.
- Fleet controllers own infrastructure reconciliation and run separately from FastAPI.
  Provider adapters consume assigned instance endpoints; they do not scale fleets.
- NATS Core is for live coordination and fan-out. JetStream is for durable observation
  delivery and replay. Harbor has no Redis dependency.
- Prometheus metrics must use bounded labels. Domains, URLs, session IDs, and arbitrary
  error text belong in PostgreSQL or the observation stream, never metric labels.

## Product invariants

- DEBUG events are filtered, normalized observations of what happened—not diagnoses,
  recommendations, or routing decisions.
- Keep sensitive headers, credentials, cookies, query values, and page data out of
  events unless an explicit, tested redaction policy permits them.
- No-browser execution initially covers `page.goto`, `page.content`, and the
  declarative `Emulation.setScriptExecutionDisabled` setting. Every other
  non-bootstrap command triggers a real browser journey; add static behavior one
  command at a time with tests.
- The optimizer minimizes browser, proxy, and helper-service spend subject to
  correctness. Cost reduction never outranks correct acquisition.
- Harbor owns browser fleet policy, desired capacity, placement, health, and draining.
  Docker, Kubernetes, or another platform supplies compute through a separate Harbor
  fleet controller.
- Administrative fleet limits are not downstream session settings and cannot be
  overridden through `harbor.*` query parameters.
- Prefer the smallest implementation that satisfies the current milestone. Roadmap
  documents describe direction, not permission to build speculative abstractions.

## Development workflow

Use Python 3.13+, async I/O, FastAPI, SQLAlchemy, Alembic, PostgreSQL, and NATS.
Dependencies are managed with `uv`.

```bash
uv sync
docker compose up --build -d
uv run pytest
uv run ruff check .
```

Run infrastructure-dependent tests explicitly:

```bash
HARBOR_E2E=1 uv run pytest -m e2e
```

Before handing off a change:

1. Add or update tests at the closest appropriate level.
2. Run targeted tests, then the broader unit suite and Ruff when practical.
3. Add an Alembic migration for schema changes; do not rewrite applied migrations.
4. Update documentation when a public contract, invariant, or provider capability
   changes.
5. Preserve unrelated work in the working tree.

The programs in `examples/` are mock downstream clients and acceptance targets. Keep
them free of Harbor-specific SDK code.

## Frontend

- Build the frontend with React, TypeScript, Vite, and shadcn/ui components.
- Use React Query for server state. Keep shared API types under `web/src/types/`.
- Prefer named exports throughout `web/src/`; `App.tsx` is the sole default-export
  exception.
- Every mutation error must be passed through `extractApiError` and surfaced with
  `toast.error()`.

Run frontend checks from `web/`:

```bash
npm run lint
npm run typecheck
npm run build
```

## Implementation style

- Keep route handlers thin and domain logic independently testable.
- Use typed contracts at subsystem boundaries.
- Make cleanup, release, event publication, and redelivery-safe consumers idempotent.
- Write lifecycle events to the transactional outbox. Never wait for NATS or JetStream
  on the admission path.
- Treat provider connections and DEBUG consumers as backpressured streams with bounded
  failure behavior.
- Store the requested settings, resolved settings, source of each setting, and policy
  version whenever planning behavior evolves.
- Do not add native-provider escape hatches or speculative provider-specific public APIs.
- Leave intentionally scaffolded routes explicit rather than returning misleading
  placeholder data.
