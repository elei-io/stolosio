---
title: Security & networking
description: Understand access protection and the trusted-network deployment boundary.
---

Stolosio is experimental software for local development and trusted networks. There are no supported production releases or guaranteed security response times yet.

## Protect every service surface

CDP, DEBUG, administrative, and metrics endpoints have **no application-level access control**. Do not expose them directly to the internet.

Keep the installation on a trusted network or behind access controls supplied by your platform. Protect the API independently of the admin interface: protecting the admin hostname alone does not protect a separately reachable API Service. Ensure any proxy supports WebSocket upgrades for CDP and DEBUG, and streaming for the admin activity feed.

TLS protects transport; it does not by itself restrict who can connect. A session reference UUID is a correlation value, not an authentication token.

## Isolate browser workloads

Browser clients can execute code and request network resources. Run workers on an isolated network without access to cloud metadata, sensitive internal services, or host credentials.

The domain blocklist is an operator feature. It is not an SSRF defense or a browser sandbox. The Docker fleet controller can control Docker and should run only on a trusted host.

## Protect secrets and data

The local Compose stack binds published ports to localhost and uses development database credentials. Do not reuse those credentials for shared installations.

Keep provider credentials and connection strings in ignored local environment files or deployment Secrets. Avoid publishing live session data. Filtered observations are redacted, but that is not a guarantee that arbitrary application data is safe to share.

## Report vulnerabilities privately

Use GitHub's private **Report a vulnerability** option in the repository Security tab when available. If unavailable, ask for a private reporting channel without posting exploit details or sensitive information.

The repository's [security policy](https://github.com/elei-io/stolosio/blob/main/SECURITY.md) is the authoritative policy.
