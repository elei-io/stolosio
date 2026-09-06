#!/usr/bin/env bash
set -euo pipefail

image="${1:?Usage: container-test.sh IMAGE}"
container="$(docker run --detach --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --publish 127.0.0.1::8080 "$image")"
cleanup() { docker rm --force "$container" >/dev/null; }
trap cleanup EXIT
port="$(docker port "$container" 8080/tcp | cut -d: -f2)"
origin="http://127.0.0.1:${port}"
for attempt in {1..30}; do
  if curl --silent --fail "$origin/healthz" >/dev/null; then break; fi
  sleep 1
done
curl --silent --show-error --fail "$origin/healthz"
curl --silent --show-error --fail "$origin/" | grep 'Your browser fleet' >/dev/null
curl --silent --show-error --fail "$origin/docs/quickstart/" | grep 'Run your first session' >/dev/null
curl --silent --show-error --fail "$origin/pagefind/pagefind.js" >/dev/null
for path in /missing-page/ /v1/connect /pagefind/missing.js /_astro/missing.js; do
  status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$origin$path")"
  test "$status" = 404 || { echo "Expected 404 for $path, got $status"; exit 1; }
done
curl --silent --head "$origin/docs/" | grep -qi 'Cache-Control: no-cache'
echo 'Container checks passed: homepage, docs, search assets, cache policy, and real 404s.'
