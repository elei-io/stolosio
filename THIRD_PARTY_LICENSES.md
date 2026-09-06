# Third-party licensing

The root MIT license applies to Stolosio's original code. It does not relicense its
dependencies, fonts, container images, or external services.

## Browserless

Stolosio connects to Browserless over CDP and configures a separately running
`ghcr.io/browserless/chromium` container. Browserless's current upstream
[license](https://github.com/browserless/browserless/blob/main/LICENSE) offers
SSPL-1.0 or a Browserless commercial license. An MIT license for Stolosio does not
grant rights to Browserless under MIT or resolve obligations under Browserless's terms.

Review the license attached to the exact image version you deploy or distribute.
The development configuration currently uses `latest`, so this document cannot certify
a fixed image's license. Commercial or hosted use requires particular attention to
the upstream terms; do not assume that a separate container removes those obligations.

## Packages and assets

Python dependencies are recorded in `uv.lock`; frontend dependencies are recorded in
`web/package-lock.json`. Their license files and notices remain authoritative.
The initial metadata review found mainly MIT, BSD, ISC, and Apache licenses, plus
MPL-2.0 dependencies, OFL-1.1 fonts, and CC-BY-4.0 material.

When redistributing packages, compiled frontend assets, or container images, retain
required copyright and license notices and satisfy any applicable attribution or
source-availability requirements. Review the actual licenses for the versions and
assets included in that distribution. This summary is not a complete third-party
notice bundle or a completed redistribution audit.

Browserbase is an external service governed by its own service terms. PostgreSQL,
NATS, Chromium, and other software in deployment images also retain their own terms.
