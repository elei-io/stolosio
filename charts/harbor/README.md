# Harbor Helm chart

This chart installs Harbor application processes, the Harbor fleet controller, its
namespace-scoped RBAC, API and UI Services, and the static Browserless workload
template.

It deliberately does not install PostgreSQL, NATS, Ingress, Gateway API, DNS, TLS,
cert-manager, Prometheus, a Prometheus Operator, monitoring CRDs, KEDA, or an HPA.

Released charts are available at:

```text
oci://ghcr.io/ekkuleivonen/charts/harbor
```

The chart defaults to the matching version of `ghcr.io/ekkuleivonen/harbor` and
`ghcr.io/ekkuleivonen/harbor-web`. Browserless is pulled directly from
`ghcr.io/browserless/chromium` at the version pinned in `values.yaml`; Harbor does not
republish it.

## Required values

```yaml
database:
  existingSecret: harbor-connections
  urlSecretKey: database-url

nats:
  existingSecret: harbor-connections
  urlSecretKey: nats-url
  seedSecretKey: nats-seed
```

The Secret must already exist in the release namespace.
`nats.seedSecretKey` is optional for password or token authenticated NATS
deployments. Set it for an NKey seed stored in the same Secret.

Fleet limits and Browserless session capacity are administered through Harbor and
stored in PostgreSQL. They are not Helm values.

The PostgreSQL identity must be able to apply Harbor's schema migrations. The NATS
identity must be able to manage Harbor's own JetStream resources. The chart runs
migrations as a pre-install and pre-upgrade Helm Job and retains a successful Job for
`migration.ttlSecondsAfterFinished`; it never provisions PostgreSQL or NATS.

Optional Browserbase credentials are also read from an existing Secret:

```yaml
browserbase:
  existingSecret: harbor-browserbase
  apiKeySecretKey: api-key
  projectIdSecretKey: project-id
```

Global `imagePullSecrets`, `podAnnotations`, `podLabels`, `nodeSelector`, `affinity`,
and `tolerations` apply to Harbor application Pods. Browserless has corresponding
settings under `browserless.*`. `browserless.timeoutMilliseconds` configures the
worker-side maximum session lifetime; fleet limits and per-instance concurrency still
come from PostgreSQL.

Set `monitoring.enabled=true` to create annotated ClusterIP metrics Services for the
API and fleet controller. The Services expose `/metrics` and carry standard
`prometheus.io/*` discovery annotations. If the platform has already installed the
Prometheus Operator CRDs, `monitoring.serviceMonitor.enabled=true` additionally creates
a `ServiceMonitor`. Harbor never installs the operator, Prometheus, or its CRDs.

The chart creates no Ingress. API and UI Services default to `LoadBalancer`, and can
be changed to `ClusterIP` when the platform supplies its own exposure layer. Keep all
image tags pinned when overriding the chart defaults. The web service proxies the
dashboard's HTTP API requests, but intentionally returns `404` for the
`/v1/connect` route family; CDP clients must use the API service.

See [the Kubernetes deployment guide](../../docs/KUBERNETES.md) for ownership,
networking, scaling, and installation details.
