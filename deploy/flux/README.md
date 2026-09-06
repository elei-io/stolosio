# Flux example

This directory consumes Stolosio's released OCI Helm chart from GHCR. It assumes:

- Flux source-controller and helm-controller are installed;
- a `v0.1.15` (or compatible) Stolosio release has published the chart and images;
- PostgreSQL and NATS are reachable from the cluster;
- `stolosio-connections` and, while GHCR packages are private, `ghcr-auth` exist in
  the `stolosio` namespace.

Create a classic GitHub personal access token with `read:packages`, then create the
registry Secret:

```bash
kubectl create namespace stolosio
kubectl --namespace stolosio create secret docker-registry ghcr-auth \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<github-token>
```

The same Secret authenticates Flux's `OCIRepository` and Stolosio's application image
pulls. If all three Stolosio GHCR packages are public, remove `secretRef` from
`source.yaml`, remove `imagePullSecrets` from `release.yaml`, and do not create
`ghcr-auth`.

Create `stolosio-connections` with the two externally managed service URLs:

```bash
kubectl --namespace stolosio create secret generic stolosio-connections \
  --from-literal=database-url='postgresql+asyncpg://stolosio:password@postgres.example:5432/stolosio' \
  --from-literal=nats-url='tls://nats.example:4222' \
  --from-literal=nats-seed='SU...'
```

For a real GitOps repository, commit both Secrets encrypted with SOPS or generate them
with an external-secrets controller; do not commit plaintext credentials. Then add
this directory to the Flux `Kustomization` path or copy it into the cluster repository.

After reconciliation:

```bash
flux get sources oci --namespace stolosio
flux get helmreleases --namespace stolosio
kubectl get services --namespace stolosio
```

The chart defaults to `LoadBalancer` Services for both the API and UI. k3s ServiceLB,
MetalLB, or another platform load-balancer implementation must allocate their
addresses.
