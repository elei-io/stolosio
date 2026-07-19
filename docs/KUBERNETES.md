# Kubernetes and k3s

Harbor runs the managed Browserless fleet itself. PostgreSQL remains authoritative for
fleet policy, desired capacity, queues, leases, draining, and instance observations.
Kubernetes supplies Pods, stable service discovery, scheduling, and container
recovery. Do not attach an HPA or KEDA `ScaledObject` to Harbor's Browserless
StatefulSet.

The same integration works on Kubernetes and k3s through the standard in-cluster API.
It does not call Docker, containerd, or a distribution-specific CLI.

## Platform prerequisites

The platform must provide reachable PostgreSQL and NATS installations and put their
connection URLs in a Kubernetes Secret. Harbor does not install, operate, size, back
up, or upgrade either service. The PostgreSQL identity must be allowed to run Harbor's
schema migrations. The NATS identity must be allowed to create and update Harbor's
own JetStream stream and consumers.

The Helm chart does not create Ingress, Gateway API, DNS, or TLS resources. It creates
separate API and UI Services. They default to `LoadBalancer`; a platform may instead
select `ClusterIP` and expose them through its own networking layer.

The cluster also needs:

- Flux source-controller and helm-controller when using the GitOps example;
- k3s ServiceLB, MetalLB, or another implementation for `LoadBalancer` addresses;
- GHCR pull credentials while the Harbor image and chart packages are private;
- nodes matching the architecture of every pinned image;
- enough node memory for the configured Browserless requests and limits.

Harbor's application and UI images are published for `linux/amd64` and `linux/arm64`.
Confirm that the Browserless version you pin also publishes an image for every node
architecture on which it may be scheduled.

## Fleet ownership

The Helm release owns a ConfigMap containing the static Browserless workload template.
The Harbor fleet controller reads that template and owns the resulting StatefulSet.
This keeps Helm and GitOps tools from competing with Harbor over replicas or
Browserless concurrency.

Harbor owns these dynamic values:

- StatefulSet replicas;
- `CONCURRENT`, derived from PostgreSQL session capacity;
- the applied workload revision;
- Pod draining and replacement.

Helm owns the Browserless image, resources, probes, security context, and scheduling
constraints. The StatefulSet uses `OnDelete`, so applying a changed template never
restarts an existing Pod automatically. A scale-up may create new Pods from the latest
template while older Pods continue serving work; Harbor waits for fleet demand to
reach zero before replacing those older Pods. Session-capacity changes also wait for
zero demand.

StatefulSet ordinals make scale-down deterministic. Harbor drains the highest ordinal,
transactionally prevents new admission to it, waits for its live assignments to
finish, and only then lowers the replica count.

Kubernetes restarts failed containers through the Pod liveness policy. Harbor
currently treats a Pod that remains not Ready beyond the configured startup timeout
as unhealthy, stops admitting work to it, and deletes it after its assignments have
cleared. The StatefulSet creates a clean replacement. Per-instance semantic signals
such as repeated CDP handshake or browser-launch failures are not yet wired into this
replacement decision.

## Published artifacts

GitHub Actions publishes:

```text
ghcr.io/ekkuleivonen/harbor
ghcr.io/ekkuleivonen/harbor-web
oci://ghcr.io/ekkuleivonen/charts/harbor
```

The Harbor image is shared by the API, migration Job, maintenance and health workers,
and fleet controller. Browserless remains the upstream
`ghcr.io/browserless/chromium` image. There are no Harbor-owned PostgreSQL or NATS
images.

Pushes to `main` publish `main` and immutable `sha-<short-commit>` image tags. A
semantic Git tag such as `v0.1.2` publishes matching versioned images and the OCI Helm
chart. Use the versioned release for normal GitOps; use a `sha-*` tag only when testing
an unreleased application build with the chart checked out from Git.

## Helm installation

Create a Secret containing the two connection URLs:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: harbor-connections
  namespace: harbor
type: Opaque
stringData:
  database-url: postgresql+asyncpg://harbor:password@postgres.example:5432/harbor
  nats-url: tls://nats.example:4222
  nats-seed: SU...
```

Install Harbor:

```bash
helm upgrade --install harbor \
  oci://ghcr.io/ekkuleivonen/charts/harbor \
  --version 0.1.2 \
  --namespace harbor \
  --create-namespace \
  --set database.existingSecret=harbor-connections \
  --set nats.existingSecret=harbor-connections \
  --set nats.seedSecretKey=nats-seed
```

The release chart defaults to matching Harbor and web image versions and a pinned
Browserless version. Keep all image tags pinned when overriding those defaults.

For private GHCR packages, log Helm in before a direct installation and configure an
image-pull Secret in the chart:

```bash
helm registry login ghcr.io --username <github-user>

kubectl --namespace harbor create secret docker-registry ghcr-auth \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<github-token>
```

Then add `--set imagePullSecrets[0].name=ghcr-auth` to the Helm command. The token only
needs `read:packages`.

`browserless.timeoutMilliseconds` controls the Browserless worker's session deadline.
Fleet enablement, minimum and maximum instances, queue limits, cooldown, and
per-instance concurrency remain PostgreSQL-backed Harbor administration settings.

The chart runs `alembic upgrade head` as a Helm pre-install and pre-upgrade Job using
the supplied PostgreSQL Secret. PostgreSQL and NATS themselves remain
platform-managed.

Browserbase is optional. When used, put its API key and project ID in an existing
Secret and reference it:

```yaml
browserbase:
  existingSecret: harbor-browserbase
  apiKeySecretKey: api-key
  projectIdSecretKey: project-id
```

Read the platform-assigned endpoints:

```bash
kubectl get service harbor-api harbor-web --namespace harbor
```

The public CDP endpoint is:

```text
ws://<API-ADDRESS>:8411/v1/connect
```

The platform may attach DNS and TLS and publish the endpoint as `wss://`. Pod IPs are
never public Harbor endpoints. The UI Service proxies its same-origin `/v1` requests
to the internal API Service; the API Service remains separately exposed for CDP
clients.

## Flux installation

The example under [`deploy/flux`](../deploy/flux) uses a Flux `OCIRepository` and
`HelmRelease` to consume the versioned chart directly from GHCR. Copy that directory
into the cluster GitOps repository, provide `harbor-connections`, and provide
`ghcr-auth` while the packages are private.

Keep credentials out of plaintext Git. Encrypt the Secrets with SOPS or source them
from the platform's external-secrets mechanism. The same `ghcr-auth` Docker config
Secret can authenticate both Flux's chart pull and the Harbor application image
pulls.

Flux reconciles the static Helm release and Browserless workload template. Harbor's
fleet controller still owns the live Browserless StatefulSet replicas and rollout;
do not add that StatefulSet to the GitOps repository and do not attach KEDA or an HPA
to it.

## Controller availability

The initial chart runs one fleet-controller replica. Reconciliation is idempotent, but
the complete reconcile pass is not yet protected by a durable leader lease. Increase
the controller replica count only after provider-scoped leader election is added.
If the controller restarts, existing sessions continue, but scaling, rollout, and
replacement pause until it resumes.
