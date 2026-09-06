---
title: Providers & compatibility
description: Understand HTTP acquisition, native CDP browser providers, and escalation boundaries.
---

| Path        | Intended role                                  | Capacity owner                       |
| ----------- | ---------------------------------------------- | ------------------------------------ |
| HTTP        | Bounded navigation and HTML retrieval          | Stolosio admission                   |
| Browserless | Default browser path, managed horizontal fleet | Stolosio instances and slots         |
| Browserbase | Optional external browser capacity             | Stolosio's configured external quota |

## HTTP has a deliberately small surface

The no-browser facade covers `page.goto`, `page.content`, the exact bootstrap commands those operations need, and declarative `Emulation.setScriptExecutionDisabled` state for replay.

Every other non-bootstrap command requires a browser. Transport failure, non-success status, invalid HTML headers, excessive response size, or failed content sanity can also trigger browser acquisition.

With forced HTTP selection, unsupported behavior returns a protocol error. Stolosio does not silently substitute materially different results.

## Native CDP passthrough

Browserless and Browserbase receive opaque CDP traffic. Stolosio preserves command IDs, session IDs, event order, backpressure, and close behavior. The selected browser determines whether an individual command is supported.

One browser attempt owns one upstream browser session. Independent sessions can occupy separate slots on the same Browserless worker.

## Escalation boundaries

An automatic session can escalate from HTTP to one browser. Stolosio replays safe state, checks document readiness, and switches execution. Unsafe side effects are not replayed.

After browser acquisition, Stolosio does not switch browser providers. Exhausting the acquisition plan returns an explicit error.

## Browserbase and spend

Automatic routing requires explicit paid-fallback permission as well as enabled quota. Browserbase is tried only after local candidates are exhausted. It is not automatically probed, and it is never the primary automatic provider.

See [client settings](/docs/clients/) and the detailed [provider contract](https://github.com/elei-io/stolosio/blob/main/docs/PROVIDERS.md).
