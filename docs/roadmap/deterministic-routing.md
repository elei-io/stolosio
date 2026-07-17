# Deterministic Domain Routing

Status: implemented

## Contract

- Unknown and unqualified domains start on the PostgreSQL-configured default provider,
  which is Camoufox until an operator changes it.
- Enabled providers advertise a rankable cost rate; capability manifests remain in code.
- New domains are probed once against every cheaper provider.
- Existing eligible sessions are sampled at a configurable basis-point rate, default 1%.
- Probes run only `page.goto()` and `page.content()` and passively count console errors.
- A strict, versioned comparison controls qualification.
- Automatic routing chooses the cheapest qualified provider and promotes unsupported
  commands to the configured default after ordered replay.
- Explicit `harbor.provider.slug` continues to override automatic routing.

## Minimal persistence

The milestone adds four tables:

- `routing_configuration`
- `provider_routing_profiles`
- `domain_provider_profiles`
- `qualification_probes`

Detailed commands, responses, headers, console fingerprints, and promotions remain in
`session_events`. Cost is recorded on existing `acquisition_attempts`; no duplicate
evidence tables or cost ledger are introduced.

The qualification worker claims PostgreSQL rows with a lease and exercises Harbor using
ordinary Playwright `connect_over_cdp()`. Its probe UUID is the existing client session
reference, allowing the gateway to run the candidate through the exact adaptive path
without adding a public probe setting.

## Sampling

The first eligible acquisition atomically increments the domain's eligible acquisition
count and always schedules probes. Later acquisitions use:

```text
sha256(session_id + domain_id) mod 10,000 < configured basis points
```

Tests use 0% or 100% and never depend on probabilistic timing.

## Exit condition

An unknown domain uses the conservative default; matching background probes qualify a
cheaper provider; a later ordinary CDP session selects it; and a candidate that fails
through the adaptive path remains rejected.
