"""Run with uv run python tests/runtime/check.py after building both runtime images.

The apparently public subnet is an isolated Docker bridge, never a remote test host.
All containers/networks are disposable and removed on both success and failure.
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.async_api import Error, async_playwright

PREFIX = f"stolosio-egress-{uuid4().hex[:8]}"
PUBLIC = "93.184.216.10"
PRIVATE = "172.30.96.10"
ROOT = Path(__file__).resolve().parent
containers = []
networks = []


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True
    ).stdout.strip()


def create(name, image, *args):
    name = f"{PREFIX}-{name}"
    containers.append(name)
    docker(
        "create",
        "--name",
        name,
        "--network",
        f"{PREFIX}-public",
        "--dns",
        PUBLIC,
        "--mount",
        f"type=bind,src={ROOT},dst=/tests,readonly",
        *args,
        image,
    )
    return name


def port(name, port):
    return docker("port", name, str(port)).rsplit(":", 1)[1]


def check_startup_requires_firewall():
    for suffix in ("browserless", "fetch-proxy"):
        name = f"{PREFIX}-unprotected-{suffix}"
        containers.append(name)
        result = subprocess.run(
            ["docker", "run", "--name", name, f"stolosio-{suffix}:egress-test"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0
        assert "Operation not permitted" in result.stderr, result.stderr


async def main():
    await asyncio.to_thread(check_startup_requires_firewall)
    for suffix, subnet in [("public", "93.184.216.0/24"), ("private", "172.30.96.0/24")]:
        name = f"{PREFIX}-{suffix}"
        docker("network", "create", "--subnet", subnet, name)
        networks.append(name)
    server = f"{PREFIX}-server"
    containers.append(server)
    docker(
        "create",
        "--name",
        server,
        "--network",
        f"{PREFIX}-public",
        "--ip",
        PUBLIC,
        "-p",
        "127.0.0.1::80",
        "--mount",
        f"type=bind,src={ROOT},dst=/tests,readonly",
        "--entrypoint",
        "/usr/bin/python3",
        "stolosio-fetch-proxy:egress-test",
        "/tests/server.py",
    )
    docker("network", "connect", "--ip", PRIVATE, f"{PREFIX}-private", server)
    docker("start", server)
    browser = create(
        "browser",
        "stolosio-browserless:egress-test",
        "--cap-add",
        "NET_ADMIN",
        "-e",
        "TOKEN=egress-test",
        "-e",
        "TIMEOUT=120000",
        "-e",
        "DEBUG=-*",
        "-p",
        "127.0.0.1::3000",
    )
    proxy = create(
        "proxy",
        "stolosio-fetch-proxy:egress-test",
        "--cap-add",
        "NET_ADMIN",
        "-p",
        "127.0.0.1::3128",
    )
    for name in (browser, proxy):
        docker("network", "connect", f"{PREFIX}-private", name)
        docker("start", name)
        docker("exec", "-d", "-e", "CANARY=1", name, "python3", "/tests/server.py")
    browser_url = f"ws://127.0.0.1:{port(browser, 3000)}?token=egress-test"
    proxy_url = f"http://127.0.0.1:{port(proxy, 3128)}"
    async with httpx.AsyncClient(trust_env=False) as control:
        deadline = time.monotonic() + 45
        while True:
            try:
                response = await control.get(browser_url.replace("ws:", "http:") + "&ready=1")
                if response.status_code < 500:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise AssertionError("Browser runtime failed to start")
            await asyncio.sleep(0.5)
        print("Testing HTTP proxy", flush=True)
        async with httpx.AsyncClient(
            proxy=proxy_url, trust_env=False, follow_redirects=True
        ) as client:
            assert (await client.get("http://public.test/")).status_code == 200
            for destination in [
                f"http://{PRIVATE}/",
                "http://127.0.0.1/",
                "http://[::1]/",
                f"http://[::ffff:{PRIVATE}]/",
                "http://private.test/",
                "http://public.test/redirect",
            ]:
                response = await client.get(destination)
                assert response.status_code == 403, (destination, response.status_code)
                assert response.headers["x-squid-error"].startswith("ERR_ACCESS_DENIED")
            try:
                await client.get(f"https://{PRIVATE}/")
                raise AssertionError("Private CONNECT succeeded")
            except httpx.ProxyError:
                pass
        print("Testing Chromium", flush=True)
        async with async_playwright() as playwright:
            remote = await playwright.chromium.connect_over_cdp(browser_url)
            page = await remote.new_page()
            await page.goto("http://public.test/")
            assert await page.title() == "Public fixture"
            for destination in [
                f"http://{PRIVATE}/",
                "http://127.0.0.1/",
                "http://[::1]/",
                f"http://[::ffff:{PRIVATE}]/",
                "http://private.test/",
                "http://public.test/redirect",
            ]:
                blocked_page = await remote.new_page()
                try:
                    await blocked_page.goto(destination, timeout=5000)
                    raise AssertionError(f"Private navigation succeeded: {destination}")
                except Error:
                    pass
                finally:
                    await blocked_page.close()
            await page.goto("http://public.test/")
            print("Testing subresources", flush=True)
            results = await page.evaluate(
                """async (privateIP) => {
              const url = `http://${privateIP}/`;
              return await Promise.all([
                fetch(url + 'fetch').then(() => false, () => true),
                new Promise(r => {
                  const f = document.createElement('iframe');
                  f.onload = () => r(true);
                  f.src = url + 'iframe';
                  document.body.append(f);}),
                new Promise(r => {
                  const i = new Image();
                  i.onload = () => r(false);
                  i.onerror = () => r(true);
                  i.src = url + 'image';}),
                new Promise(r => {
                  const s = document.createElement('script');
                  s.onload = () => r(false);
                  s.onerror = () => r(true);
                  s.src = url + 'script';
                  document.body.append(s);}),
                new Promise(r => {
                  const w = new WebSocket(`ws://${privateIP}/socket`);
                  w.onopen = () => r(false);
                  w.onerror = () => r(true);}),
                new Promise(r => {
                  const w = new Worker('/worker.js');
                  w.onmessage = () => {w.terminate(); r(true);};
                  w.onerror = () => r(false);})
              ]);
            }""",
                PRIVATE,
            )
            assert all(results), results
            print("Testing DNS rebinding", flush=True)
            await page.goto("http://rebind.test/")
            await remote.close()
            docker(
                "exec",
                server,
                "python3",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1/rebind').read()",
            )
            remote = await playwright.chromium.connect_over_cdp(browser_url)
            page = await remote.new_page()
            try:
                await page.goto("http://rebind.test/", timeout=5000)
                raise AssertionError("Rebound private address was reached")
            except Error:
                pass
            await remote.close()
        counts = json.loads(
            docker(
                "exec",
                server,
                "python3",
                "-c",
                "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1/stats').read().decode())",
            )
        )
        assert counts.get(PUBLIC, 0) > 0, counts
        assert counts.get(PRIVATE, 0) == 0, counts
        for name in (browser, proxy):
            assert docker("exec", name, "cat", "/tmp/canary-count") == "0"
        # The unprivileged fetch identity cannot alter the installed policy.
        for name, user in [(browser, "blessuser"), (proxy, "proxy")]:
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "exec", "--user", user, name, "nft", "flush", "ruleset"],
                capture_output=True,
            )
            assert result.returncode != 0
        for name, user in [(browser, "blessuser"), (proxy, "proxy")]:
            # Exercise raw sockets too: proves the packet boundary independently of
            # Chromium's own private-network checks and Squid's destination ACL.
            docker(
                "exec",
                "--user",
                user,
                name,
                "python3",
                "-c",
                "import socket; "
                "s=socket.socket(); s.settimeout(2); "
                f"assert s.connect_ex(('{PRIVATE}',80)) != 0",
            )
            status = docker("exec", name, "cat", "/proc/1/status")
            capabilities = {
                line.split(":")[0]: int(line.split()[1], 16)
                for line in status.splitlines()
                if line.startswith(("CapEff:", "CapBnd:"))
            }
            assert all(not (value & (1 << 12 | 1 << 13)) for value in capabilities.values())
        counts = json.loads(
            docker(
                "exec",
                server,
                "python3",
                "-c",
                "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1/stats').read().decode())",
            )
        )
        assert counts.get(PRIVATE, 0) == 0, counts
        print(
            "PASS: public access; private navigation, redirects, subresources, WebSocket, "
            "worker, IPv6 literals, DNS rebinding; zero canary connections"
        )


async def bounded_main():
    async with asyncio.timeout(120):
        await main()


try:
    asyncio.run(bounded_main())
finally:
    for name in reversed(containers):
        docker("rm", "-f", name, check=False)
    for name in reversed(networks):
        docker("network", "rm", name, check=False)
