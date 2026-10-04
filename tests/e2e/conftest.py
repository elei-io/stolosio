import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


@pytest.fixture(scope="session", autouse=True)
def managed_browser_controller() -> Iterator[None]:
    if os.getenv("STOLOSIO_E2E") != "1":
        yield
        return

    root = Path(__file__).resolve().parents[2]
    api = os.getenv("STOLOSIO_E2E_HTTP_URL", "http://localhost:8411")
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "backend.fleet.controllers.docker",
            "--no-metrics",
        ],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    originals = {}
    providers = {"browserless": 2}
    for provider in providers:
        original_response = httpx.get(f"{api}/v1/admin/fleets/{provider}", timeout=5)
        original_response.raise_for_status()
        originals[provider] = original_response.json()

    def update(
        provider: str,
        maximum_instances: int,
        *,
        capacity: int,
        cooldown_seconds: int = 1,
        minimum_instances: int = 1,
        enabled: bool = True,
    ) -> None:
        response = httpx.patch(
            f"{api}/v1/admin/fleets/{provider}",
            json={
                "minimum_instances": minimum_instances,
                "maximum_instances": maximum_instances,
                "session_capacity_per_instance": capacity,
                "scale_down_cooldown_seconds": cooldown_seconds,
                "enabled": enabled,
            },
            headers={"X-Stolosio-Actor": "e2e-controller-fixture"},
            timeout=5,
        )
        response.raise_for_status()

    def wait_for_one_instance(provider: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = httpx.get(f"{api}/v1/fleet/providers", timeout=5)
            response.raise_for_status()
            fleet = next(value for value in response.json() if value["provider"] == provider)
            if (
                fleet["desired_instances"] == 1
                and fleet["observed_instances"] == 1
                and fleet["ready_instances"] == 1
            ):
                return
            time.sleep(0.25)
        raise TimeoutError(f"{provider} fleet did not converge to one ready instance")

    try:
        for provider, capacity in providers.items():
            update(provider, 1, capacity=capacity)
            wait_for_one_instance(provider)
        for provider, capacity in providers.items():
            update(provider, 4, capacity=capacity)
        yield
    finally:
        try:
            for provider, capacity in providers.items():
                update(provider, 1, capacity=capacity)
                wait_for_one_instance(provider)
                original = originals[provider]
                update(
                    provider,
                    original["maximum_instances"],
                    capacity=original["session_capacity_per_instance"],
                    cooldown_seconds=original["scale_down_cooldown_seconds"],
                    minimum_instances=original["minimum_instances"],
                    enabled=original["enabled"],
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    "stolosio",
                    "up",
                    "-d",
                    "--scale",
                    "browserless=1",
                    "--no-recreate",
                    "browserless",
                ],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


@pytest.fixture(scope="module")
def local_challenge_site():
    """Temporary origin on a Docker-only public-address subnet: egress policy stays intact."""
    network = f"stolosio-challenge-e2e-{uuid4().hex[:8]}"
    container = network + "-origin"
    connected = []

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True).strip()

    # Special-use IPs are intentionally refused by the real egress firewall. This subnet
    # exists only inside Docker; fixture traffic cannot leave it. No firewall is relaxed.
    docker("network", "create", "--subnet", "11.254.254.0/24", network)
    try:
        docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--network",
            network,
            "--ip",
            "11.254.254.2",
            "--network-alias",
            container,
            "--mount",
            f"type=bind,src={Path(__file__).with_name('challenge_origin.py').resolve()},dst=/origin.py,readonly",
            "python:3.13-alpine",
            "python",
            "/origin.py",
        )
        for service in ("fetch-proxy", "browserless"):
            target = docker("compose", "ps", "--quiet", service)
            assert target, f"Compose {service} must be running"
            docker("network", "connect", network, target)
            connected.append(target)
        yield f"http://{container}"
    finally:
        for target in connected:
            subprocess.run(["docker", "network", "disconnect", network, target], check=False)
        subprocess.run(["docker", "rm", "--force", container], check=False, capture_output=True)
        subprocess.run(["docker", "network", "rm", network], check=False, capture_output=True)
