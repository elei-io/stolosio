# Kubernetes and k3s

Stolosio runs the managed Browserless fleet itself. PostgreSQL remains authoritative for
fleet policy, desired capacity, queues, leases, draining, and instance observations.
Kubernetes supplies Pods, stable service discovery, scheduling, and container
recovery. Do not attach an HPA or KEDA `ScaledObject` to Stolosio's Browserless
StatefulSet.

The same integration works on Kubernetes and k3s through the standard in-cluster API.
It does not call Docker, containerd, or a distribution-specific CLI.

## Platform prerequisites

The platform must provide reachable PostgreSQL and NATS installations and put their
connection details in namespace-scoped Kubernetes Secrets. Stolosio does not install, operate, size, back
up, or upgrade either service. The PostgreSQL identity must be allowed to run Stolosio's
schema migrations. The NATS identity must be allowed to create and update Stolosio's
own JetStream streams and consumers. The platform does not create or delete those
resources.

NATS is a disposable delivery layer for Stolosio. PostgreSQL is authoritative. Stolosio's
maintenance worker continuously reconciles its streams and consumers and reconstructs
the retained event window from PostgreSQL whenever it creates a fresh event stream.

The Helm chart does not create Ingress, Gateway API, DNS, or TLS resources. It creates
separate API and UI Services. They default to `LoadBalancer`; a platform may instead
select `ClusterIP` and expose them through its own networking layer.

The cluster also needs:

- Flux source-controller and helm-controller when using the GitOps example;
- k3s ServiceLB, MetalLB, or another implementation for `LoadBalancer` addresses;
- GHCR pull credentials while the Stolosio image and chart packages are private;
- nodes matching the architecture of every pinned image;
- enough node memory for the configured Browserless requests and limits.

Stolosio's application and UI images are published for `linux/amd64` and `linux/arm64`.
Confirm that the Browserless version you pin also publishes an image for every node
architecture on which it may be scheduled.

## Fleet ownership

The Helm release owns a ConfigMap containing the static Browserless workload template.
The Stolosio fleet controller reads that template and owns the resulting StatefulSet.
This keeps Helm and GitOps tools from competing with Stolosio over replicas or
Browserless concurrency.

Stolosio owns these dynamic values:

- StatefulSet replicas;
- `CONCURRENT`, derived from PostgreSQL session capacity;
- the applied workload revision;
- Pod draining and replacement.

Helm owns the Browserless image, resources, probes, security context, and scheduling
constraints. The StatefulSet uses `OnDelete`, so applying a changed template never
restarts an existing Pod automatically. A scale-up may create new Pods from the latest
template while older Pods continue serving work; Stolosio waits for fleet demand to
reach zero before replacing those older Pods. Session-capacity changes also wait for
zero demand.

StatefulSet ordinals make scale-down deterministic. Stolosio drains the highest ordinal,
transactionally prevents new admission to it, waits for its live assignments to
finish, and only then lowers the replica count.

Kubernetes restarts failed containers through the Pod liveness policy. Stolosio
currently treats a Pod that remains not Ready beyond the configured startup timeout
as unhealthy, stops admitting work to it, and deletes it after its assignments have
cleared. The StatefulSet creates a clean replacement. Per-instance semantic signals
such as repeated CDP handshake or browser-launch failures are not yet wired into this
replacement decision.

## Published artifacts

GitHub Actions publishes:

```text
ghcr.io/elei-io/stolosio
ghcr.io/elei-io/stolosio-admin
oci://ghcr.io/elei-io/charts/stolosio
```

The Stolosio image is shared by the API, migration Job, maintenance and health workers,
and fleet controller. The matching `stolosio-browserless` image wraps pinned upstream Chromium with
a mandatory outbound firewall. `stolosio-fetch-proxy` isolates HTTP page fetching.
Both images need `NET_ADMIN` at startup, which their entrypoints drop before serving
requests. See [Network policy](NETWORK_POLICY.md). There are no Stolosio-owned PostgreSQL or NATS
images.

Pushes to `main` publish `main` and immutable `sha-<short-commit>` image tags. A
semantic Git tag such as `v0.1.15` publishes matching versioned images and the OCI Helm
chart. Use the versioned release for normal GitOps; use a `sha-*` tag only when testing
an unreleased application build with the chart checked out from Git.

## Helm installation

Create separate Secrets for the PostgreSQL connection and the platform-issued Stolosio
NATS namespace credential:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: stolosio-database
  namespace: stolosio
type: Opaque
stringData:
  DATABASE_URL: postgresql+asyncpg://stolosio:password@postgres.example:5432/stolosio
---
apiVersion: v1
kind: Secret
metadata:
  name: nats-stolosio
  namespace: stolosio
type: Opaque
stringData:
  NATS_URL: tls://nats.example:4222
  NATS_SEED: SU...
```

Install Stolosio:

```bash
helm upgrade --install stolosio \
  oci://ghcr.io/elei-io/charts/stolosio \
  --version 0.1.15 \
  --namespace stolosio \
  --create-namespace \
  --set database.existingSecret=stolosio-database \
  --set database.urlSecretKey=DATABASE_URL \
  --set nats.existingSecret=nats-stolosio \
  --set nats.urlSecretKey=NATS_URL \
  --set nats.seedSecretKey=NATS_SEED \
  --set nats.jetstreamReplicas=3
```

The release chart defaults to matching Stolosio and admin image versions and a pinned
Browserless version. Keep all image tags pinned when overriding those defaults.

For private GHCR packages, log Helm in before a direct installation and configure an
image-pull Secret in the chart:

```bash
helm registry login ghcr.io --username <github-user>

kubectl --namespace stolosio create secret docker-registry ghcr-auth \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<github-token>
```

Then add `--set imagePullSecrets[0].name=ghcr-auth` to the Helm command. The token only
needs `read:packages`.

`browserless.timeoutMilliseconds` controls the Browserless worker's session deadline.
Fleet enablement, minimum and maximum instances, queue limits, cooldown, and
per-instance concurrency remain PostgreSQL-backed Stolosio administration settings.

The chart runs `alembic upgrade head` as a Helm pre-install and pre-upgrade Job using
the supplied PostgreSQL Secret. PostgreSQL and NATS themselves remain
platform-managed.

Browserbase is optional. When used, put its API key and project ID in an existing
Secret and reference it:

```yaml
browserbase:
  existingSecret: stolosio-browserbase
  apiKeySecretKey: api-key
  projectIdSecretKey: project-id
```

Read the platform-assigned endpoints:

```bash
kubectl get service stolosio-api stolosio-admin --namespace stolosio
```

The public CDP endpoint is:

```text
ws://<API-ADDRESS>:8411/v1/connect
```

The platform may attach DNS and TLS and publish the endpoint as `wss://`. Pod IPs are
never public Stolosio endpoints. The UI Service proxies its same-origin `/v1` requests
to the internal API Service; the API Service remains separately exposed for CDP
clients.

## Flux installation

The example under [`deploy/flux`](../deploy/flux) uses a Flux `OCIRepository` and
`HelmRelease` to consume the versioned chart directly from GHCR. Copy that directory
into the cluster GitOps repository, provide `stolosio-database` and `nats-stolosio`, and provide
`ghcr-auth` while the packages are private.

Keep credentials out of plaintext Git. Encrypt the Secrets with SOPS or source them
from the platform's external-secrets mechanism. The same `ghcr-auth` Docker config
Secret can authenticate both Flux's chart pull and the Stolosio application image
pulls.

Flux reconciles the static Helm release and Browserless workload template. Stolosio's
fleet controller still owns the live Browserless StatefulSet replicas and rollout;
do not add that StatefulSet to the GitOps repository and do not attach KEDA or an HPA
to it.

## Controller availability

The initial chart runs one fleet-controller replica. Reconciliation is idempotent, but
the complete reconcile pass is not yet protected by a durable leader lease. Increase
the controller replica count only after provider-scoped leader election is added.
If the controller restarts, existing sessions continue, but scaling, rollout, and
replacement pause until it resumes.
