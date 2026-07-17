# Domain Support Routing

Status: implemented.

Harbor builds an independent domain/provider support matrix from absolute navigation,
HTTP, header, method-coverage, and content-sanity checks. A leased PostgreSQL worker
checks all enabled providers. Support evidence is versioned by policy and capability
manifest.

Automatic runtime planning orders supported providers by cost and appends the
configured default as the final compatible candidate. When no provider is known to be
supported, the configured default is the plan. Runtime transitions exclude attempted
providers and try the full remaining plan. No baseline provider or cross-provider
content comparison participates in the decision.

Acceptance coverage includes configured-default routing, cheapest-supported ordering
with a final default, expensive alternatives, JS app shells ruling out HTTP, dynamic
meaningful content, version invalidation, and explicit failure after the full plan is
exhausted.
