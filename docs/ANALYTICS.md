# Domain Provider Eligibility

Harbor maintains two independent facts for every domain/provider pair:

1. **Acquisition health** — can the provider navigate to the domain and return a
   healthy-looking document?
2. **Runtime compatibility** — has a real automatic session shown that the provider
   can execute its exact CDP command shapes?

A provider is eligible for automatic routing only when it is enabled, its current
health evidence is healthy, and its runtime state is not suppressed. Cost orders
eligible providers; it never overrides either correctness condition.

## Health probes

A leased background worker checks every enabled provider independently. Probes record
only bounded facts:

- navigation completion;
- a healthy main-response status;
- HTML-compatible headers rather than a download;
- meaningful content without an obvious error page, JavaScript-required warning, or
  empty application shell.

Probes never compare providers, retain HTML, inspect historical CDP methods, or decide
runtime command compatibility. Dynamic content is expected. Probe sessions remain
visible in Activity but do not feed domain command or cost projections.

Health is versioned by the health policy and provider contract. Operators can queue
one or all providers from the domain UI. Manual probes reuse the latest probe-safe URL,
deduplicate active work, and remain repeatable after completion.

## Runtime compatibility

Automatic sessions evaluate exact method-and-parameter shapes against the same
provider contracts used by execution. Raw parameters remain process-local. PostgreSQL
stores only the compatible boolean, an optional method name, session/domain identity,
timestamps, and contract version.

When the current provider cannot execute a command, Harbor suppresses that
domain/provider pair immediately, before attempting a transition. Suppression remains
binding even if every transition candidate fails.

At successful automatic-session close, Harbor records counterfactual compatibility
for every configured provider:

- an incompatible result keeps or creates suppression;
- one compatible session restores a suppressed provider only when that session first
  saw the domain after the suppression.

The time condition prevents the escalating session, or an older concurrent session,
from undoing the evidence that caused suppression. Explicit-provider sessions, health
probes, and failed sessions do not change runtime state. Historical method aggregates
are telemetry only.

## Runtime plan

For a known domain, Harbor orders current healthy and runtime-eligible providers by
observed average attempt cost, falling back to configured cost. It does not append an
otherwise ineligible default.

The configured `default_provider` is only the bootstrap route when the domain has no
current healthy evidence. It is not a correctness baseline.

During a replay-safe transition Harbor may hold the active source attempt while it
acquires one replacement. The source is released only after the destination is active,
replay succeeds, and the triggering command is accepted. Plan exhaustion is an
explicit protocol error.

Explicit `harbor.provider.slug` selection remains an override. It neither transitions
automatically nor rewrites runtime compatibility.

## Storage and administration

PostgreSQL owns:

- `domain_provider_health` and `health_probes`;
- `domain_provider_runtime_state`;
- `session_domain_provider_compatibility`;
- `domain_provider_cost_stats`;
- routing configuration and factual transition statistics.

DEBUG contains normalized facts only. Health, compatibility, and route plans are
policy conclusions presented outside DEBUG.

```text
POST /v1/admin/domains/{domain_id}/probes
{"providers": ["http", "lightpanda"]}
```

Omit `providers` to queue every configured provider.
