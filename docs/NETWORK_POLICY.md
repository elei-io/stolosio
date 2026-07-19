# Network policy

Harbor owns one global, PostgreSQL-backed domain blocklist. It is administrative
policy, not a downstream `harbor.*` session setting, and clients cannot override it.
Startup creates the missing global row with an empty blocklist but never replaces
saved values from environment configuration.

Operators read and replace the policy through:

```text
GET   /v1/admin/network
PATCH /v1/admin/network
```

The PATCH body contains the complete desired list:

```json
{
  "blocked_domain_patterns": [
    "*.doubleclick.net",
    "ads.example"
  ]
}
```

Patterns are case-insensitive hostnames. A leading `*.` is the only supported
wildcard and matches one or more subdomain levels, so `*.doubleclick.net` matches
`ads.doubleclick.net` and `a.b.doubleclick.net` but not the apex
`doubleclick.net`. Add both patterns when both forms should be blocked. Schemes,
ports, paths, and embedded wildcards are rejected.

The global policy and its configuration version are snapshotted into every provider
acquisition attempt. An update affects newly acquired attempts; it does not mutate an
already-running browser attempt.

Each API process caches the PostgreSQL policy for 60 seconds, so session admission
does not read the database for every connection. An update through the local process
refreshes its cache immediately. Other API replicas can continue using their prior
snapshot until their cache expires.

For native browser providers, Harbor converts the domain patterns to CDP URL
patterns and applies `Network.setBlockedURLs` to each attached page, iframe, worker,
and service-worker target before exposing that attachment to the downstream client.
The Harbor-owned command response is consumed internally; downstream command IDs,
session IDs, and event order remain unchanged. If the provider rejects or times out
while applying the policy, Harbor closes the connection with
`domain_blocking_unavailable` rather than continuing without the requested policy.

The HTTP provider does not fetch page subresources. It applies the same policy to
top-level `Page.navigate` requests and returns an explicit protocol error for a
matching destination.
