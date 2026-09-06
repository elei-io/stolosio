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
`ghcr.io/elei-io/stolosio-admin`. Browserless is pulled directly from
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
image tags pinned when overriding the chart defaults. The admin service proxies the
dashboard's HTTP API requests, but intentionally returns `404` for the
`/v1/connect` route family; CDP clients must use the API service.

See [the Kubernetes deployment guide](../../docs/KUBERNETES.md) for ownership,
networking, scaling, and installation details.

## Grafana overview and alerts

With `monitoring.enabled=true`, opt in to `monitoring.dashboard.enabled=true`
and `monitoring.prometheusRule.enabled=true` when the platform provides Grafana
sidecar discovery and Prometheus Operator CRDs. Both are disabled by default.
The ConfigMap uses `grafana_dashboard: "1"`; customize `monitoring.dashboard.labels`
and `monitoring.prometheusRule.labels` to match the platform's discovery selectors.
Set `monitoring.dashboard.adminUrl` for the administrative drill-down link.

The overview covers demand/capacity, browser fleet response, acquisition and CDP
outcomes, event delivery, Kubernetes resources and Loki logs. Select Prometheus and
Loki data sources at the top; namespace and release scope follow the Helm release.
Kubernetes panels require kubelet and kube-state-metrics; logs require Loki with a
`namespace` label. Resource and log panels cover the release namespace; use a dedicated
namespace. Provider filters affect provider-specific panels; gateway, command-domain
latency, transitions, event delivery and resources retain their documented global scope.

Shared database gauges use the maximum across API replicas, not their sum. Counters
and histogram buckets aggregate process-local rates. Empty traffic produces no success
percentage or percentile, not an invented healthy value. Counts are scrape estimates.
Acquisition success measures an upstream connection, not crawler extraction success.
The recorder age is sampled at batch processing time and is not an idle-time alarm.

Five conservative alerts cover API unavailability, stale fleet reconciliation,
maintenance unavailability, pending recorder work without progress, and dead letters.
The platform owns Alertmanager routing and notifications. Queue, error-rate and latency
SLO thresholds should follow representative workload measurements. Review thresholds
when changing scrape cadence or fleet-controller timing.
