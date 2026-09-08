# Network policy

Stolosio owns one global, PostgreSQL-backed domain blocklist. It is administrative
policy, not a downstream `stolosio.*` session setting, and clients cannot override it.
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

For native browser providers, Stolosio converts the domain patterns to CDP URL
patterns and applies `Network.setBlockedURLs` to each attached page, iframe, worker,
and service-worker target before exposing that attachment to the downstream client.
The Stolosio-owned command response is consumed internally; downstream command IDs,
session IDs, and target-local event order remain unchanged. Stolosio configures attached
targets concurrently and holds only traffic for a target whose policy is not yet
confirmed, so an attaching worker cannot delay traffic for an already-configured page.
If the provider rejects or times out while applying the policy, Stolosio closes the
connection with `domain_blocking_unavailable` rather than continuing without the
requested policy.

The HTTP provider does not fetch page subresources. It applies the same policy to
`Page.navigate` requests and every redirect and returns an explicit protocol error for a
matching destination.

## Public-only page acquisition

The supported self-hosted runtime blocks page connections to private, loopback,
link-local, multicast, reserved and deployment-specified destinations at the packet
layer. Only public TCP ports 80/443 and the container's configured DNS resolver
(on port 53) are allowed. IPv6 is restricted to public unicast with special-use
exclusions. The policy is fixed infrastructure configuration, independent of the
operator-managed domain blocklist and downstream CDP settings.

Two runtime images implement the boundary:

- `stolosio-browserless` wraps pinned upstream Browserless. Its controller runs as
  root with limited process/ownership capabilities; Chromium runs as `blessuser`
  without capabilities and with `no_new_privs`. The wrapper transfers only the
  disposable browser profile to Chromium. Chromium cannot initiate a loopback
  connection, but can reply to a CDP connection initiated by the controller.
- `stolosio-fetch-proxy` runs Ubuntu's maintained Squid package without a disk cache
  or access logs. Squid's destination ACL produces explicit denials; the same
  packet firewall closes DNS-rebinding and destination-resolution races. Squid
  runs without capabilities after startup. It must be reachable only by the API.

Both entrypoints require `NET_ADMIN` to install rules before starting their
service, then remove it from the running processes and their descendants. A failed
rule installation aborts startup. Do not grant privileged mode, host networking,
or runtime access to Docker sockets. The runtime files must not be writable by
Chromium. Container exec by an administrator remains a privileged operation.

`HTTP_FETCH_PROXY_URL` is required for HTTP page requests (local default:
`http://localhost:3128`). Compose and Helm configure it automatically. Environment
proxy variables are ignored. Proxy errors and network-policy denials return a
terminal protocol error; they do not escalate to a browser. Ordinary origin status
or content failures still follow the existing escalation rules. Every HTTP redirect
is checked against the attempt's domain blocklist before it is sent.

The Helm value `egress.extraBlockedCidrs` adds deployment-specific address ranges,
including infrastructure using public IPs. For standalone containers, set the same
space-separated list in `EGRESS_EXTRA_BLOCKED_CIDRS`. There is no allow-private
switch or per-session exception. DNS resolvers are read from `/etc/resolv.conf` at
startup; recreate containers after changing resolver or interface configuration.

Kubernetes NetworkPolicy remains an outer boundary and must restrict ingress to
Browserless and the fetch proxy. It cannot replace the in-container rules for
localhost. Deployments using Browserbase or other externally managed browser
endpoints must independently verify their equivalent isolation before enabling
those providers; the self-hosted firewall does not protect remote browsers.
Browserbase acquisition is disabled unless the deployment explicitly sets
`BROWSERBASE_NETWORK_ISOLATION_VERIFIED=true` (Helm:
`browserbase.networkIsolationVerified`). This is an operator assertion after
verification, not a mechanism that hardens the external provider.

### Verification

From the repository root:

```sh
docker build -f runtime/Dockerfile.browserless -t stolosio-browserless:egress-test runtime
docker build -f runtime/Dockerfile.fetch-proxy -t stolosio-fetch-proxy:egress-test runtime
uv run python tests/runtime/check.py
uv run pytest tests/proxy/test_provider_transition.py
```

The container test uses disposable bridges and endpoints, checks successful public
navigation and blocked private requests, and counts connections received by private
and loopback canaries. The public-looking fixture addresses belong only to its local
Docker bridge; they are not remote test targets. Containers and networks are removed
on success or failure. Run the test on Linux Docker (Docker Desktop also supplies a
Linux VM). Run it when updating Browserless, Squid, the firewall, or kernel/CNI behavior.
