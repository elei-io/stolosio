import json
from datetime import UTC, datetime

import pytest

from backend.fleet import RuntimeInstance
from backend.fleet.runtimes import DockerCommandError, DockerComposeRuntime


@pytest.mark.asyncio
async def test_docker_runtime_lists_compose_service_as_runtime_instances(tmp_path) -> None:
    runtime = DockerComposeRuntime(workdir=tmp_path, project_name="stolosio")
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    async def run(*command: str) -> str:
        if "inspect" not in command:
            return "container-id\n"
        return json.dumps(
            [
                {
                    "Name": "/stolosio-browserless-1",
                    "State": {"Running": True, "StartedAt": started_at},
                    "Config": {"Env": ["CONCURRENT=17"]},
                }
            ]
        )

    runtime._run = run  # type: ignore[method-assign]

    instances = await runtime.list_instances("browserbase")

    assert instances == [
        RuntimeInstance(
            instance_id="container-id",
            address="stolosio-browserless-1",
            started_at=datetime.fromisoformat(started_at.replace("Z", "+00:00")),
            session_capacity=17,
        )
    ]


@pytest.mark.asyncio
async def test_docker_runtime_treats_failed_port_probe_as_not_ready(tmp_path) -> None:
    runtime = DockerComposeRuntime(workdir=tmp_path, project_name="stolosio")

    async def fail(*command: str) -> str:
        raise DockerCommandError("not listening")

    runtime._run = fail  # type: ignore[method-assign]

    ready = await runtime.port_open(RuntimeInstance("id", "container"), 9222)

    assert ready is False
