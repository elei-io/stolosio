# stolosio-public

The static public homepage and operator documentation for Stolosio. Built with Astro,
Starlight, TypeScript, and locally bundled Geist fonts. Independent of `stolosio-admin`
and the backend; it makes no requests to an operator's live installation.

Requires Node.js 22.12+ (Node 24 in CI) and npm.

```bash
cd public-site
npm ci
npm run dev
```

Open http://127.0.0.1:4321. Documentation starts at `/docs/`.

## Validate and preview the production site

```bash
npm run typecheck
npm run build
npm test
npm run preview
```

Pagefind search is generated at build time; use the production preview to test it.
`npm test` validates built pages, internal links and anchors, metadata, and search assets.
Deploy the contents of `dist/` to a static host that serves directory `index.html` files
and uses `404.html` for missing routes. No server runtime or instance credentials are needed.
Set Astro's `site` URL when choosing a production domain to enable canonical sitemap URLs.

## Content

- `src/pages/index.astro`: custom marketing homepage.
- `src/content/docs/docs/`: public guides, served under `/docs/`.
- `src/styles/`: homepage and documentation themes.
- `astro.config.mjs`: navigation and Starlight configuration.

Public guides explain current supported behavior. Repository `docs/` files retain
in-depth implementation contracts; link to them for details rather than duplicating
full specifications. Update operator guides whenever the relevant behavior changes.
Do not turn roadmap items into current capability claims. Use only sample data in
future screenshots; the public site must not connect to a private admin deployment.

## Container artifact

The image is published as `ghcr.io/elei-io/stolosio-public` by the repository's
Publish workflow. It serves static files with Nginx on port 8080 and runs as a
non-root user. It has no backend dependency or API proxy.

```bash
docker build -t stolosio-public:local public-site
bash public-site/scripts/container-test.sh stolosio-public:local
```

Run those commands from the repository root.

The image targets `linux/amd64`. Pushes to `main` publish `main` and
`sha-<short-commit>` tags; version releases also publish versioned tags. Pull
requests validate builds without publishing. Pin a commit tag or image digest in
your infrastructure repository for reproducible deployments.

### Runtime contract

- HTTP listens on port `8080`; `/healthz` returns HTTP 200.
- The homepage is `/` and documentation is `/docs/`.
- Runs as UID/GID `101:101` and supports a read-only root filesystem with writable
  temporary storage mounted at `/tmp`.
- No backend, database, NATS, credentials, or persistent storage is required.

Deployment manifests, scheduling, resource limits, ingress, DNS, and TLS belong to
the infrastructure repository.
