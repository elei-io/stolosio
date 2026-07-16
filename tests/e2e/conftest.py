import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="session", autouse=True)
def managed_chromium_controller() -> Iterator[None]:
    if os.getenv("HARBOR_E2E") != "1":
        yield
        return

    root = Path(__file__).resolve().parents[2]
    api = os.getenv("HARBOR_E2E_HTTP_URL", "http://localhost:8411")
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
    original_response = httpx.get(f"{api}/v1/admin/fleets/chromium", timeout=5)
    original_response.raise_for_status()
    original = original_response.json()

    def update(
        maximum_instances: int,
        *,
        capacity: int = 2,
        cooldown_seconds: int = 1,
    ) -> None:
        response = httpx.patch(
            f"{api}/v1/admin/fleets/chromium",
            json={
                "minimum_instances": 1,
                "maximum_instances": maximum_instances,
                "session_capacity_per_instance": capacity,
                "scale_down_cooldown_seconds": cooldown_seconds,
            },
            headers={"X-Harbor-Actor": "e2e-controller-fixture"},
            timeout=5,
        )
        response.raise_for_status()

    def wait_for_one_instance() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = httpx.get(f"{api}/v1/fleet/providers", timeout=5)
            response.raise_for_status()
            chromium = response.json()[0]
            if (
                chromium["desired_instances"] == 1
                and chromium["observed_instances"] == 1
                and chromium["ready_instances"] == 1
            ):
                return
            time.sleep(0.25)
        raise TimeoutError("Chromium fleet did not converge to one ready instance")

    try:
        update(1)
        wait_for_one_instance()
        update(4)
        yield
    finally:
        try:
            update(1)
            wait_for_one_instance()
            update(
                original["maximum_instances"],
                capacity=original["session_capacity_per_instance"],
                cooldown_seconds=original["scale_down_cooldown_seconds"],
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
                    "harbor",
                    "up",
                    "-d",
                    "--scale",
                    "chromium=1",
                    "--no-recreate",
                    "chromium",
                ],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
