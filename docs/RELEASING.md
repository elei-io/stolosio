# CI and releases

GitHub Actions provides two paths:

- `CI` applies migrations to PostgreSQL, runs backend lint and tests with
  JetStream-enabled NATS, runs frontend lint/type-check/build, builds both containers,
  and validates the Helm/Kubernetes manifests on pull requests and pushes to `main`.
- `Publish` builds multi-architecture `linux/amd64` and `linux/arm64` images and pushes
  them to GHCR on `main`, semantic version tags, and manual runs.

The published Stolosio artifacts are:

```text
ghcr.io/elei-io/stolosio
ghcr.io/elei-io/stolosio-admin
oci://ghcr.io/elei-io/charts/stolosio
```

The first image is shared by the API, workers, migration Job, and fleet controller.
Browserless remains the upstream `ghcr.io/browserless/chromium` image. PostgreSQL and
NATS are external services, not Stolosio images.

## Tags

A `main` build publishes `main` and `sha-<short-commit>` image tags. A tag such as
`v0.1.15` publishes image tags `0.1.15`, `0.1`, `sha-<short-commit>`, and `latest`, then
publishes the Helm chart as OCI version `0.1.15`. Pre-release tags do not move `latest`.
A manual run always publishes the immutable commit tag and may also publish its branch
tag.

The chart is only published for a Git tag. The workflow requires the Git tag without
its `v` prefix, the Python project version, `charts/stolosio/Chart.yaml` `version` and
`appVersion`, and the chart's default Stolosio image tags to match. This check runs
before any release image is pushed.

To release `0.1.15` after CI passes:

```bash
git tag v0.1.15
git push origin v0.1.15
```

GitHub's repository `GITHUB_TOKEN` publishes all artifacts; no long-lived publishing
credential is required. New packages follow the repository/package visibility
configuration. For a private package, a homelab needs a classic personal access token
with `read:packages`. Alternatively, make the three packages public after their first
publication.

Enable all `CI` checks as required checks on the default branch before treating a
release as supported. GitHub Actions dependencies are commit-pinned and Dependabot is
configured to propose their updates.

## Experimental release policy

Current versions are experimental and do not promise API or database compatibility.
Document breaking changes in release notes. Before declaring a deployment supported,
stop rewriting its applied migrations and provide forward migrations for retained data.
A public repository alone does not make a release production-supported.

Before making the repository public, review dependency licenses and redistribution notices, enable private vulnerability
reporting, and review Git history and published
artifacts for secrets or private data. Automated scans supplement that review.

Version 0.1.15 names the operator UI `stolosio-admin`. Helm values are
`admin` and `adminImage`; the source directory is `admin/`. The former web package
is retired. Use 0.1.15 or a pinned newer Git chart with matching images.
