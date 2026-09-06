---
title: Deploy on Kubernetes & k3s
description: Install the Stolosio Helm chart with external PostgreSQL and NATS.
---

The Helm chart installs Stolosio, its fleet controller, and the template for the managed Browserless workload. It supports Kubernetes and k3s through the standard Kubernetes API.

## Prepare the platform

You need:

- Reachable PostgreSQL, with an identity permitted to run schema migrations.
- NATS with JetStream, with an identity permitted to manage Stolosio's streams and consumers.
- A namespace, connection Secrets, and sufficient browser memory capacity.
- Your own network access protection, DNS, and TLS where required.

The chart does not install PostgreSQL or NATS and does not configure ingress, DNS, or TLS. Its API and admin Services default to `LoadBalancer`. Choose `ClusterIP` for internal-only services where appropriate. Read [security and networking](/docs/security/) first.

## Create connection Secrets

```bash
kubectl create namespace stolosio
```

Provide the following Secrets through your platform's secret-management mechanism. These values are examples; replace them with your own service endpoints and credentials.

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
  NATS_SEED: SU_REPLACE_WITH_PLATFORM_CREDENTIAL
```

Keep real credentials out of plaintext Git. Use encrypted Secrets or an external-secrets mechanism.

## Install a matching release

Choose a published version from [GitHub releases](https://github.com/elei-io/stolosio/releases). Use matching chart and application versions. For example, the repository's documented release is `0.1.12`:

```bash
helm upgrade --install stolosio \
  oci://ghcr.io/elei-io/charts/stolosio \
  --version 0.1.12 \
  --namespace stolosio \
  --set database.existingSecret=stolosio-database \
  --set database.urlSecretKey=DATABASE_URL \
  --set nats.existingSecret=nats-stolosio \
  --set nats.urlSecretKey=NATS_URL \
  --set nats.seedSecretKey=NATS_SEED
```

The chart runs migrations as a pre-install and pre-upgrade Job. Keep image tags pinned. Confirm that your chosen Browserless image supports your nodes' architectures.

If artifacts require authentication, configure Helm registry access and image-pull Secrets as described in the [repository deployment guide](https://github.com/elei-io/stolosio/blob/main/docs/KUBERNETES.md).

## Verify your deployment

```bash
kubectl get pods --namespace stolosio
kubectl get jobs --namespace stolosio
kubectl get service stolosio-api stolosio-admin --namespace stolosio
```

Check that the migration Job completes and application Pods are Ready. Browserless capacity depends on fleet policy and demand; zero workers can be valid when the configured minimum is zero.

Connect a client through the API address using `/v1/connect`. Open the separate admin Service to inspect fleet health and session results. With TLS terminated by your platform, use `wss://` for CDP.

## Fleet ownership

Stolosio's controller owns live Browserless replicas, concurrency, draining, and rollout. **Do not attach an HPA or KEDA ScaledObject to the Browserless StatefulSet.** Helm owns its static template, including image, resources, and scheduling constraints.

Keep the fleet controller at one replica. Durable leader election for multiple active controller replicas is not yet implemented.

## Flux

The [Flux example](https://github.com/elei-io/stolosio/tree/main/deploy/flux) provides an OCIRepository and HelmRelease. Copy them into your GitOps repository and provide the required Secrets. Flux manages the Helm release; Stolosio continues to own live browser scaling.
