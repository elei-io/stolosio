---
title: Operate your fleet
description: Manage capacity, provider queues, routing policy, and paid fallback.
---

The protected admin interface for an installation is the operator's control surface. In local development it is available at `http://localhost:5173`.

## Understand capacity

A logical session consumes global Stolosio capacity. Acquisition attempts enter provider queues and receive browser slots. Sessions do not own a permanent provider.

Managed fleets consist of browser instances with session slots. Only healthy, ready, non-draining instances contribute available capacity. Browserless capacity is based on instances multiplied by configured session slots per instance.

## Set fleet policy

Use the admin interface to inspect and adjust fleet enablement, minimum and maximum instances, queue limits, cooldown, and per-instance concurrency.

PostgreSQL is authoritative for saved policy. Environment variables carry credentials, endpoints, and runtime mechanics; they do not reconcile or overwrite saved fleet limits. Client query parameters cannot override administrative fleet limits.

Begin with limits your host can support. Watch readiness, queue pressure, and browser memory before raising concurrency.

## Keep the controller running

The fleet controller is separate from the API. It reconciles desired browser capacity through Docker or Kubernetes. If it stops, existing sessions can continue, but scaling, replacement, and rollout pause.

In Kubernetes, scale-down drains the highest ordinal first and waits for live assignments to finish. Template replacement and session-capacity changes wait for zero demand. See the [deployment guide](/docs/kubernetes/).

## Automatic routing

Automatic sessions can begin with HTTP. Failed HTTP checks or unsupported commands trigger browser acquisition. Domain-level evidence from sessions and background probes informs future starting routes.

Once a browser is acquired, the session stays with that browser provider. Browser-to-browser live migration is not supported.

## Paid fallback is explicit

Browserbase requires configured credentials and provider capacity. Automatic sessions must also opt in using `stolosio.provider.allow_paid_fallback=true`. Local candidates are exhausted first.

Browserbase is never the primary automatic provider and is not probed automatically. An operator can explicitly request a paid diagnostic probe. Direct Browserbase selection remains available within admission limits.

## Domain blocking

The same operator-managed domain blocklist applies to browser attempts. Stolosio returns `domain_blocking_unavailable` if it cannot apply the policy. The HTTP path checks top-level navigation and has no subresource requests to filter.

This policy does not replace network isolation. See [security](/docs/security/).
