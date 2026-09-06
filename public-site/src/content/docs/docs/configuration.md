---
title: Configuration
description: Distinguish client settings, saved operator policy, and runtime configuration.
---

## Three configuration surfaces

| Surface                    | What belongs here                                               | Where to change it                                  |
| -------------------------- | --------------------------------------------------------------- | --------------------------------------------------- |
| Client connection settings | Provider selection, paid-fallback permission, session reference | `stolosio.*` query parameters on the connection URL |
| Operator policy            | Fleet bounds, provider capacity, queues, routing                | Admin interface, persisted in PostgreSQL            |
| Runtime configuration      | Credentials, service endpoints, ports, process mechanics        | Environment or deployment Secrets and chart values  |

Explicit query settings override Stolosio's automatic plan, which overrides defaults. Client settings cannot override administrative capacity limits.

## Common connection settings

| Setting                                 | Purpose                                                                                 |
| --------------------------------------- | --------------------------------------------------------------------------------------- |
| `stolosio.provider.slug`                | Explicitly select `http`, `browserless`, or `browserbase`. Omit for automatic planning. |
| `stolosio.provider.allow_paid_fallback` | Set to `true` to permit automatic paid fallback after local candidates.                 |
| `stolosio.session.reference`            | Supply a UUID to correlate a CDP connection with the DEBUG stream.                      |

```text
ws://localhost:8411/v1/connect?stolosio.provider.slug=browserless
```

The connection route stays `/v1/connect` for all providers. Provider addresses are internal implementation details.

## Environment configuration

Use the checked-in [`.env.example`](https://github.com/elei-io/stolosio/blob/main/.env.example) as the source for supported runtime overrides. Keep secrets in an ignored `.env` file for local development.

The admin host port is controlled by `STOLOSIO_ADMIN_PORT`. Saved fleet and routing policy are not overwritten from environment variables at startup.

## Helm values

Consult the [chart values](https://github.com/elei-io/stolosio/blob/main/charts/stolosio/values.yaml) for the version you deploy. The chart accepts existing database, NATS, and optional Browserbase Secrets. It also defines static workload settings such as images, resources, and scheduling constraints.

Stolosio's live browser replica count and concurrency are owned by its fleet controller and saved policy.
