# Stolosio Helm chart

This chart installs Stolosio application processes, the Stolosio fleet controller, its
namespace-scoped RBAC, API and UI Services, and the static Browserless workload
template.

It deliberately does not install PostgreSQL, NATS, Ingress, Gateway API, DNS, TLS,
cert-manager, Prometheus, a Prometheus Operator, monitoring CRDs, KEDA, or an HPA.

Released charts are available at:

```text
oci://ghcr.io/elei-io/charts/stolosio
```

The chart defaults to the matching version of `ghcr.io/elei-io/stolosio` and
`ghcr.io/elei-io/stolosio-web`. Browserless is pulled directly from
`ghcr.io/browserless/chromium` at the version pinned in `values.yaml`; Stolosio does not
republish it.

## Required values

```yaml
database:
  existingSecret: stolosio-database
  urlSecretKey: DATABASE_URL

nats:
  existingSecret: nats-stolosio
  urlSecretKey: NATS_URL
  seedSecretKey: NATS_SEED
  jetstreamReplicas: 3
```

The Secrets must already exist in the release namespace.
`nats.seedSecretKey` is optional for password or token authenticated NATS
deployments. Set it for an NKey seed stored in the same Secret.

Fleet limits and Browserless session capacity are administered through Stolosio and
stored in PostgreSQL. They are not Helm values.

The PostgreSQL identity must be able to apply Stolosio's schema migrations. The NATS
identity must be able to manage Stolosio's own JetStream resources. The chart runs
migrations as a pre-install and pre-upgrade Helm Job and retains a successful Job for
`migration.ttlSecondsAfterFinished`; it never provisions PostgreSQL or NATS. Stolosio
creates and continuously reconciles its own streams and consumers. When it creates a
fresh event stream, it reconstructs the retained event window from PostgreSQL.

Optional Browserbase credentials are also read from an existing Secret:

```yaml
browserbase:
  existingSecret: stolosio-browserbase
  apiKeySecretKey: api-key
  projectIdSecretKey: project-id
```

Global `imagePullSecrets`, `podAnnotations`, `podLabels`, `nodeSelector`, `affinity`,
and `tolerations` apply to Stolosio application Pods. `workloadAnnotations` applies to
Deployment metadata and can be used by secret operators that restart workloads after
credential rotation. Browserless has corresponding
settings under `browserless.*`. `browserless.timeoutMilliseconds` configures the
worker-side maximum session lifetime; fleet limits and per-instance concurrency still
come from PostgreSQL.

Set `monitoring.enabled=true` to create annotated ClusterIP metrics Services for the
API, maintenance worker, and fleet controller. The Services expose `/metrics` and carry standard
`prometheus.io/*` discovery annotations. If the platform has already installed the
Prometheus Operator CRDs, `monitoring.serviceMonitor.enabled=true` additionally creates
a `ServiceMonitor`. Stolosio never installs the operator, Prometheus, or its CRDs.

The chart creates no Ingress. API and UI Services default to `LoadBalancer`, and can
be changed to `ClusterIP` when the platform supplies its own exposure layer. Keep all
image tags pinned when overriding the chart defaults. The web service proxies the
dashboard's HTTP API requests, but intentionally returns `404` for the
`/v1/connect` route family; CDP clients must use the API service.

See [the Kubernetes deployment guide](../../docs/KUBERNETES.md) for ownership,
networking, scaling, and installation details.
