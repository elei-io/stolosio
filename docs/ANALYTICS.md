# Deterministic Domain Routing

Harbor uses historical observations to choose the cheapest provider that has been
qualified for a domain. This is deterministic policy, not machine learning.

Unknown and unqualified domains use the operator-selected default provider. After an
eligible `page.goto()` plus `page.content()` session completes, Harbor probes cheaper
providers through its normal adaptive CDP path. It compares status, selected headers,
console-error count, and a SHA-256 content fingerprint without retaining HTML.

Every new domain schedules one probe per cheaper enabled provider. Existing domains
sample completed eligible sessions at an operator-configured basis-point rate, initially
`100` (1%). Sampling uses a stable session/domain hash and is reproducible.

A provider becomes qualified after the configured number of matching probes. A mismatch
or execution failure rejects it. Future sessions choose the qualified provider with the
lowest historical average cost, falling back to its configured cost rate when no actual
cost exists.

Provider capabilities remain versioned adapter manifests. Costs, the conservative
default, probe rate, and required matches live in PostgreSQL and are available through
the development admin API:

```text
GET/PATCH /v1/admin/routing
GET       /v1/admin/routing/providers
PATCH     /v1/admin/routing/providers/{provider}
```

The policy deliberately makes false negatives cheap and false positives difficult:
strict comparison may retain an expensive provider, but a direct-provider success does
not qualify a provider unless the same acquisition also works through Harbor's adaptive
replay path.

Detailed facts remain in the DEBUG evidence stream. Qualification is a policy conclusion
stored separately and never presented as a DEBUG recommendation.
