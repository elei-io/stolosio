# CI and releases

GitHub Actions provides two paths:

- `CI` applies migrations to PostgreSQL, runs backend lint and tests with
  JetStream-enabled NATS, runs frontend lint/type-check/build, builds both containers,
  and validates the Helm/Kubernetes manifests on pull requests and pushes to `main`.
- `Publish` builds multi-architecture `linux/amd64` and `linux/arm64` images and pushes
  them to GHCR on `main`, semantic version tags, and manual runs.

The published Harbor artifacts are:

```text
ghcr.io/ekkuleivonen/harbor
ghcr.io/ekkuleivonen/harbor-web
oci://ghcr.io/ekkuleivonen/charts/harbor
```

The first image is shared by the API, workers, migration Job, and fleet controller.
Browserless remains the upstream `ghcr.io/browserless/chromium` image. PostgreSQL and
NATS are external services, not Harbor images.

## Tags

A `main` build publishes `main` and `sha-<short-commit>` image tags. A tag such as
`v0.1.1` publishes image tags `0.1.1`, `0.1`, `sha-<short-commit>`, and `latest`, then
publishes the Helm chart as OCI version `0.1.1`. Pre-release tags do not move `latest`.
A manual run always publishes the immutable commit tag and may also publish its branch
tag.

The chart is only published for a Git tag. The workflow requires the Git tag without
its `v` prefix, the Python project version, `charts/harbor/Chart.yaml` `version` and
`appVersion`, and the chart's default Harbor image tags to match. This check runs
before any release image is pushed.

To release `0.1.1` after CI passes:

```bash
git tag v0.1.1
git push origin v0.1.1
```

GitHub's repository `GITHUB_TOKEN` publishes all artifacts; no long-lived publishing
credential is required. New packages follow the repository/package visibility
configuration. For a private package, a homelab needs a classic personal access token
with `read:packages`. Alternatively, make the three packages public after their first
publication.

Enable all `CI` checks as required checks on the default branch before treating a
release as supported. GitHub Actions dependencies are commit-pinned and Dependabot is
configured to propose their updates.
