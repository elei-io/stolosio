import asyncio
import json
from datetime import datetime
from pathlib import Path

from backend.fleet.contracts import RuntimeInstance


class DockerCommandError(RuntimeError):
    pass


class DockerComposeRuntime:
    """Docker Compose implementation of Harbor's fleet runtime contract."""

    platform = "docker-compose"

    def __init__(self, *, workdir: Path, project_name: str) -> None:
        self._workdir = workdir
        self._project_name = project_name

    async def list_instances(self, deployment: str) -> list[RuntimeInstance]:
        output = await self._run(
            "docker",
            "compose",
            "-p",
            self._project_name,
            "ps",
            "-q",
            deployment,
        )
        instances = []
        for instance_id in (value for value in output.splitlines() if value):
            raw = await self._run("docker", "inspect", instance_id)
            payload = json.loads(raw)
            if not isinstance(payload, list) or not payload:
                continue
            container = payload[0]
            state = container.get("State", {})
            if not state.get("Running"):
                continue
            address = str(container.get("Name", "")).removeprefix("/")
            if not address:
                continue
            started_at_raw = state.get("StartedAt")
            started_at = (
                datetime.fromisoformat(str(started_at_raw).replace("Z", "+00:00"))
                if started_at_raw
                else None
            )
            instances.append(
                RuntimeInstance(
                    instance_id=instance_id,
                    address=address,
                    started_at=started_at,
                )
            )
        return sorted(instances, key=lambda instance: instance.address)

    async def scale(self, deployment: str, replicas: int) -> None:
        await self._run(
            "docker",
            "compose",
            "-p",
            self._project_name,
            "up",
            "-d",
            "--scale",
            f"{deployment}={replicas}",
            "--no-recreate",
            deployment,
        )

    async def remove(self, deployment: str, instance_id: str) -> None:
        del deployment
        await self._run("docker", "rm", "--force", instance_id)

    async def port_open(self, instance: RuntimeInstance, port: int) -> bool:
        try:
            await self._run(
                "docker",
                "exec",
                instance.instance_id,
                "/usr/bin/bash",
                "-c",
                f"exec 3<>/dev/tcp/127.0.0.1/{port}",
            )
        except DockerCommandError:
            return False
        return True

    async def _run(self, *command: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self._workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise DockerCommandError(message or f"Command failed: {command[0]}")
        return stdout.decode()
