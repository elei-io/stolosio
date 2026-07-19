# Flux example

This directory consumes Harbor's released OCI Helm chart from GHCR. It assumes:

- Flux source-controller and helm-controller are installed;
- a `v0.1.7` (or compatible) Harbor release has published the chart and images;
- PostgreSQL and NATS are reachable from the cluster;
- `harbor-connections` and, while GHCR packages are private, `ghcr-auth` exist in
  the `harbor` namespace.

Create a classic GitHub personal access token with `read:packages`, then create the
registry Secret:

```bash
kubectl create namespace harbor
kubectl --namespace harbor create secret docker-registry ghcr-auth \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<github-token>
```

The same Secret authenticates Flux's `OCIRepository` and Harbor's application image
pulls. If all three Harbor GHCR packages are public, remove `secretRef` from
`source.yaml`, remove `imagePullSecrets` from `release.yaml`, and do not create
`ghcr-auth`.

Create `harbor-connections` with the two externally managed service URLs:

```bash
kubectl --namespace harbor create secret generic harbor-connections \
  --from-literal=database-url='postgresql+asyncpg://harbor:password@postgres.example:5432/harbor' \
  --from-literal=nats-url='tls://nats.example:4222' \
  --from-literal=nats-seed='SU...'
```

For a real GitOps repository, commit both Secrets encrypted with SOPS or generate them
with an external-secrets controller; do not commit plaintext credentials. Then add
this directory to the Flux `Kustomization` path or copy it into the cluster repository.

After reconciliation:

```bash
flux get sources oci --namespace harbor
flux get helmreleases --namespace harbor
kubectl get services --namespace harbor
```

The chart defaults to `LoadBalancer` Services for both the API and UI. k3s ServiceLB,
MetalLB, or another platform load-balancer implementation must allocate their
addresses.
