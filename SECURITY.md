# Security

Stolosio is experimental software for local development and trusted networks.
There are no supported production releases or guaranteed security response times yet.

## Deployment boundary

The CDP, DEBUG, administrative, and metrics endpoints have no application-level
access control. Do not expose them directly to the internet. The development Compose
file binds published ports to localhost and uses public development database credentials.
The host-side Docker fleet controller also binds its metrics endpoint to localhost.

Browser clients can execute code and request network resources. The domain blocklist
is an operator feature, not an SSRF defense or a browser sandbox. Run workloads on an
isolated network without access to cloud metadata, sensitive internal services, or
host credentials. The fleet controller can control Docker; run it only on a trusted host.

Keep credentials in an ignored `.env` file or deployment secrets. Do not publish live
session data. Normalized events are filtered and redacted, but this is not a guarantee
that arbitrary application data is safe to share.

## Reporting

Do not post vulnerabilities or credentials in public issues. Use GitHub's private
"Report a vulnerability" option in the repository Security tab when it is available.
If private reporting is unavailable, ask for a private reporting channel in an issue
without including exploit details or sensitive information.
