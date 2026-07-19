import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from backend.fleet.contracts import RuntimeInstance

_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


class KubernetesApiError(RuntimeError):
    pass


class KubernetesRuntime:
    """Kubernetes implementation backed by a controller-owned StatefulSet."""

    platform = "kubernetes"

    def __init__(
        self,
        *,
        namespace: str,
        workload_config_map: str,
        headless_service: str,
        client: httpx.AsyncClient | None = None,
        token_path: Path | None = None,
    ) -> None:
        self._namespace = namespace
        self._workload_config_map = workload_config_map
        self._headless_service = headless_service
        self._ready: dict[str, bool] = {}
        self._pod_names: dict[str, str] = {}
        if client is None:
            self._client = self._in_cluster_client()
            self._token_path = _TOKEN_PATH
        else:
            self._client = client
            self._token_path = token_path

    async def close(self) -> None:
        await self._client.aclose()

    async def list_instances(self, deployment: str) -> list[RuntimeInstance]:
        _, revision, _ = await self._workload_template()
        response = await self._request(
            "GET",
            f"/api/v1/namespaces/{self._namespace}/pods",
            params={
                "labelSelector": (
                    "app.kubernetes.io/managed-by=harbor-fleet-controller,"
                    f"harbor.openai.com/fleet={deployment}"
                )
            },
        )
        instances: list[RuntimeInstance] = []
        ready: dict[str, bool] = {}
        pod_names: dict[str, str] = {}
        for pod in response.get("items", []):
            metadata = pod.get("metadata", {})
            if not self._owned_by_stateful_set(metadata, deployment):
                continue
            status = pod.get("status", {})
            pod_name = str(metadata.get("name", ""))
            uid = str(metadata.get("uid", ""))
            if not pod_name or not uid:
                continue
            annotations = metadata.get("annotations", {})
            pod_revision = annotations.get("harbor.openai.com/workload-revision")
            capacity_raw = annotations.get("harbor.openai.com/session-capacity")
            capacity = (
                int(capacity_raw)
                if isinstance(capacity_raw, str)
                and capacity_raw.isdigit()
                else None
            )
            is_ready = any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in status.get("conditions", [])
            )
            ready[uid] = is_ready and metadata.get("deletionTimestamp") is None
            pod_names[uid] = pod_name
            started_at_raw = status.get("startTime") or metadata.get("creationTimestamp")
            instances.append(
                RuntimeInstance(
                    instance_id=uid,
                    address=(
                        f"{pod_name}.{self._headless_service}."
                        f"{self._namespace}.svc"
                    ),
                    started_at=(
                        datetime.fromisoformat(str(started_at_raw).replace("Z", "+00:00"))
                        if started_at_raw
                        else None
                    ),
                    session_capacity=capacity,
                    configuration_stale=pod_revision != revision,
                )
            )
        self._ready = ready
        self._pod_names = pod_names
        return sorted(instances, key=lambda instance: self._ordinal(instance.address))

    async def scale(
        self,
        deployment: str,
        replicas: int,
        *,
        session_capacity: int,
    ) -> None:
        template, revision, owner = await self._workload_template()
        body = self._render_stateful_set(
            template,
            deployment=deployment,
            replicas=replicas,
            session_capacity=session_capacity,
            revision=revision,
            owner=owner,
        )
        existing = await self._request(
            "GET",
            f"/apis/apps/v1/namespaces/{self._namespace}/statefulsets/{deployment}",
            allow_not_found=True,
        )
        if existing is None:
            await self._request(
                "POST",
                f"/apis/apps/v1/namespaces/{self._namespace}/statefulsets",
                json=body,
            )
            return

        instances = await self.list_instances(deployment)
        stale = [instance for instance in instances if instance.configuration_stale]
        await self._request(
            "PATCH",
            f"/apis/apps/v1/namespaces/{self._namespace}/statefulsets/{deployment}",
            headers={"Content-Type": "application/merge-patch+json"},
            json=body,
        )
        if replicas == len(instances) and stale:
            candidate = self.scale_down_candidate(stale)
            if candidate is not None:
                await self.remove(deployment, candidate.instance_id)

    def scale_down_candidate(
        self,
        instances: list[RuntimeInstance],
    ) -> RuntimeInstance | None:
        if not instances:
            return None
        return max(instances, key=lambda instance: self._ordinal(instance.address))

    async def remove(self, deployment: str, instance_id: str) -> None:
        pod_name = self._pod_names.get(instance_id)
        if pod_name is None:
            pods = await self._request(
                "GET",
                f"/api/v1/namespaces/{self._namespace}/pods",
                params={
                    "labelSelector": (
                        "app.kubernetes.io/managed-by=harbor-fleet-controller,"
                        f"harbor.openai.com/fleet={deployment}"
                    )
                },
            )
            pod_name = next(
                (
                    str(pod.get("metadata", {}).get("name"))
                    for pod in pods.get("items", [])
                    if pod.get("metadata", {}).get("uid") == instance_id
                    and self._owned_by_stateful_set(
                        pod.get("metadata", {}),
                        deployment,
                    )
                ),
                None,
            )
        if not pod_name:
            return
        await self._request(
            "DELETE",
            f"/api/v1/namespaces/{self._namespace}/pods/{pod_name}",
            json={"preconditions": {"uid": instance_id}},
            allow_not_found=True,
        )

    async def port_open(self, instance: RuntimeInstance, port: int) -> bool:
        del port
        return self._ready.get(instance.instance_id, False)

    async def _workload_template(
        self,
    ) -> tuple[dict[str, Any], str, dict[str, str]]:
        config_map = await self._request(
            "GET",
            f"/api/v1/namespaces/{self._namespace}/configmaps/{self._workload_config_map}",
        )
        raw = config_map.get("data", {}).get("statefulset.json")
        if not isinstance(raw, str):
            raise KubernetesApiError(
                f"ConfigMap {self._workload_config_map} has no statefulset.json"
            )
        try:
            template = json.loads(raw)
        except json.JSONDecodeError as error:
            raise KubernetesApiError("Browserless StatefulSet template is invalid JSON") from error
        if not isinstance(template, dict):
            raise KubernetesApiError("Browserless StatefulSet template must be an object")
        revision = hashlib.sha256(raw.encode()).hexdigest()
        metadata = config_map.get("metadata", {})
        uid = str(metadata.get("uid", ""))
        if not uid:
            raise KubernetesApiError(
                f"ConfigMap {self._workload_config_map} has no Kubernetes UID"
            )
        return template, revision, {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "name": self._workload_config_map,
            "uid": uid,
        }

    def _render_stateful_set(
        self,
        template: dict[str, Any],
        *,
        deployment: str,
        replicas: int,
        session_capacity: int,
        revision: str,
        owner: dict[str, str],
    ) -> dict[str, Any]:
        body = json.loads(json.dumps(template))
        metadata = body.setdefault("metadata", {})
        metadata["name"] = deployment
        metadata["namespace"] = self._namespace
        metadata["ownerReferences"] = [
            {
                **owner,
                "controller": False,
                "blockOwnerDeletion": False,
            }
        ]
        spec = body.setdefault("spec", {})
        spec["replicas"] = replicas
        spec["serviceName"] = self._headless_service
        spec["updateStrategy"] = {"type": "OnDelete"}
        pod_template = spec.setdefault("template", {})
        pod_metadata = pod_template.setdefault("metadata", {})
        labels = pod_metadata.setdefault("labels", {})
        labels.update(
            {
                "app.kubernetes.io/managed-by": "harbor-fleet-controller",
                "harbor.openai.com/fleet": deployment,
            }
        )
        annotations = pod_metadata.setdefault("annotations", {})
        annotations.update(
            {
                "harbor.openai.com/session-capacity": str(session_capacity),
                "harbor.openai.com/workload-revision": revision,
            }
        )
        containers = pod_template.setdefault("spec", {}).get("containers", [])
        browserless = next(
            (container for container in containers if container.get("name") == "browserless"),
            None,
        )
        if browserless is None:
            raise KubernetesApiError(
                "Browserless StatefulSet template must contain a browserless container"
            )
        environment = browserless.setdefault("env", [])
        concurrent = next(
            (entry for entry in environment if entry.get("name") == "CONCURRENT"),
            None,
        )
        if concurrent is None:
            environment.append({"name": "CONCURRENT", "value": str(session_capacity)})
        else:
            concurrent.clear()
            concurrent.update({"name": "CONCURRENT", "value": str(session_capacity)})
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        allow_not_found: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        headers = dict(kwargs.pop("headers", {}))
        if self._token_path is not None:
            try:
                token = self._token_path.read_text().strip()
            except OSError as error:
                raise KubernetesApiError(
                    "Kubernetes service-account token is unavailable"
                ) from error
            if not token:
                raise KubernetesApiError("Kubernetes service-account token is empty")
            headers["Authorization"] = f"Bearer {token}"
        if headers:
            kwargs["headers"] = headers
        response = await self._client.request(method, path, **kwargs)
        if allow_not_found and response.status_code == 404:
            return None
        if response.is_error:
            raise KubernetesApiError(
                f"Kubernetes API {method} {path} failed with {response.status_code}"
            )
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as error:
            raise KubernetesApiError(
                "Kubernetes API returned an invalid JSON response"
            ) from error
        if not isinstance(payload, dict):
            raise KubernetesApiError("Kubernetes API returned a non-object response")
        return payload

    @staticmethod
    def _ordinal(address: str) -> int:
        pod_name = address.partition(".")[0]
        _, separator, raw = pod_name.rpartition("-")
        return int(raw) if separator and raw.isdigit() else -1

    @staticmethod
    def _owned_by_stateful_set(metadata: dict[str, Any], deployment: str) -> bool:
        return any(
            owner.get("apiVersion") == "apps/v1"
            and owner.get("kind") == "StatefulSet"
            and owner.get("name") == deployment
            and owner.get("controller") is True
            for owner in metadata.get("ownerReferences", [])
        )

    @staticmethod
    def _in_cluster_client() -> httpx.AsyncClient:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if not host or not _TOKEN_PATH.exists() or not _CA_PATH.exists():
            raise KubernetesApiError("Kubernetes in-cluster credentials are unavailable")
        authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return httpx.AsyncClient(
            base_url=f"https://{authority}:{port}",
            verify=str(_CA_PATH),
            timeout=30,
        )
