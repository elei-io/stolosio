---
title: Maintenance & upgrades
description: Preserve durable state and plan upgrades for an experimental installation.
---

## What to back up

PostgreSQL is the durable source of truth for fleet policy, sessions, leases, and retained observations. Back it up using your platform's PostgreSQL tooling and verify a restore before relying on it.

Keep deployment configuration and recoverable secret references alongside your operational runbook. Secrets need their own secure backup and recovery process.

NATS is a delivery layer. The maintenance worker reconciles streams and consumers and can reconstruct the retained event window from PostgreSQL after a fresh stream is created. This does not replace database backups.

## Before an upgrade

1. Read the release notes and breaking changes for the target version.
2. Record current image and chart versions and export the relevant deployment configuration.
3. Back up PostgreSQL and verify how to restore the matching application version with it.
4. Schedule a maintenance window and finish active browser work.
5. Keep the chart and Stolosio image versions aligned.

Current releases do not guarantee API or database compatibility. Development migrations may be replaced while the greenfield policy applies. An affected development database may need a reset; do not assume every version supports an in-place upgrade.

## Apply and verify

For Helm deployments, update the pinned chart version. The chart runs `alembic upgrade head` in its pre-upgrade Job. Inspect the migration Job, application readiness, controller health, and a fresh client session after applying the release.

Browserless template changes are reconciled by Stolosio. Its controller waits for zero demand before replacing existing workers with the updated template.

## Recovery

An application image rollback alone may be incompatible with changed database state. If an upgrade cannot be recovered in place, use your verified database backup and matching application/chart versions during a maintenance window.

Never reset a database containing data you need to retain. For disposable local development, decide explicitly whether data can be discarded before removing volumes or recreating the schema.

The repository's [release guide](https://github.com/elei-io/stolosio/blob/main/docs/RELEASING.md) describes artifact tags and the experimental release policy.
