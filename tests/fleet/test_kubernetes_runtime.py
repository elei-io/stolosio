import hashlib
import json

import httpx
import pytest

from backend.fleet.runtimes import KubernetesRuntime


def _config_map() -> dict[str, object]:
    template = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {},
        "spec": {
            "selector": {"matchLabels": {"app": "browserless"}},
            "template": {
                "metadata": {"labels": {"app": "browserless"}},
                "spec": {
                    "containers": [
                        {
                            "name": "browserless",
                            "image": "browserless:test",
                            "env": [],
                        }
                    ]
                },
            },
        },
    }
    return {
        "metadata": {"name": "workload", "uid": "config-map-uid"},
        "data": {"statefulset.json": json.dumps(template)},
    }


@pytest.mark.asyncio
async def test_kubernetes_runtime_creates_controller_owned_statefulset() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/configmaps/workload"):
            return httpx.Response(200, json=_config_map())
        if request.url.path.endswith("/statefulsets/browserless"):
            return httpx.Response(404, json={})
        if request.method == "POST" and request.url.path.endswith("/statefulsets"):
            return httpx.Response(201, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
    )

    await runtime.scale("browserless", 2, session_capacity=7)

    create = next(request for request in requests if request.method == "POST")
    body = json.loads(create.content)
    assert body["metadata"]["ownerReferences"][0]["uid"] == "config-map-uid"
    assert body["spec"]["replicas"] == 2
    assert body["spec"]["updateStrategy"] == {"type": "OnDelete"}
    pod_template = body["spec"]["template"]
    assert pod_template["metadata"]["annotations"]["harbor.openai.com/session-capacity"] == "7"
    environment = pod_template["spec"]["containers"][0]["env"]
    assert {"name": "CONCURRENT", "value": "7"} in environment
    await runtime.close()


@pytest.mark.asyncio
async def test_kubernetes_runtime_maps_ready_pods_to_stable_instance_dns() -> None:
    config_map = _config_map()
    data = config_map["data"]
    assert isinstance(data, dict)
    raw = data["statefulset.json"]
    revision = hashlib.sha256(str(raw).encode()).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/configmaps/workload"):
            return httpx.Response(200, json=config_map)
        if request.url.path.endswith("/pods"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": "browserless-1",
                                "uid": "pod-uid",
                                "ownerReferences": [
                                    {
                                        "apiVersion": "apps/v1",
                                        "kind": "StatefulSet",
                                        "name": "browserless",
                                        "controller": True,
                                    }
                                ],
                                "annotations": {
                                    "harbor.openai.com/workload-revision": revision,
                                    "harbor.openai.com/session-capacity": "5",
                                },
                            },
                            "status": {
                                "startTime": "2026-07-19T00:00:00Z",
                                "conditions": [{"type": "Ready", "status": "True"}],
                            },
                        },
                        {
                            "metadata": {
                                "name": "browserless-99",
                                "uid": "foreign-pod",
                                "annotations": {
                                    "harbor.openai.com/workload-revision": revision,
                                    "harbor.openai.com/session-capacity": "5",
                                },
                            },
                            "status": {
                                "conditions": [{"type": "Ready", "status": "True"}]
                            },
                        },
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
    )

    instances = await runtime.list_instances("browserless")

    assert len(instances) == 1
    assert instances[0].address == "browserless-1.browserless-headless.harbor.svc"
    assert instances[0].session_capacity == 5
    assert await runtime.port_open(instances[0], 3000)
    assert runtime.scale_down_candidate(instances) == instances[0]
    await runtime.close()


@pytest.mark.asyncio
async def test_kubernetes_runtime_rereads_rotated_service_account_token(tmp_path) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("first-token")
    authorizations: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=_config_map())

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
        token_path=token_path,
    )

    await runtime._workload_template()
    token_path.write_text("second-token")
    await runtime._workload_template()

    assert authorizations == ["Bearer first-token", "Bearer second-token"]
    await runtime.close()


@pytest.mark.asyncio
async def test_kubernetes_runtime_scopes_pod_lookup_before_delete() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/pods"):
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
    )

    await runtime.remove("browserless", "missing-uid")

    assert (
        requests[0].url.params["labelSelector"]
        == "app.kubernetes.io/managed-by=harbor-fleet-controller,"
        "harbor.openai.com/fleet=browserless"
    )
    await runtime.close()


@pytest.mark.asyncio
async def test_kubernetes_runtime_replaces_one_stale_pod_during_stable_reconfigure() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/configmaps/workload"):
            return httpx.Response(200, json=_config_map())
        if request.url.path.endswith("/statefulsets/browserless"):
            return httpx.Response(200, json={"metadata": {"name": "browserless"}})
        if request.method == "GET" and request.url.path.endswith("/pods"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": f"browserless-{ordinal}",
                                "uid": f"pod-{ordinal}",
                                "ownerReferences": [
                                    {
                                        "apiVersion": "apps/v1",
                                        "kind": "StatefulSet",
                                        "name": "browserless",
                                        "controller": True,
                                    }
                                ],
                                "annotations": {
                                    "harbor.openai.com/workload-revision": "old",
                                    "harbor.openai.com/session-capacity": "5",
                                },
                            },
                            "status": {
                                "conditions": [{"type": "Ready", "status": "True"}]
                            },
                        }
                        for ordinal in range(2)
                    ]
                },
            )
        if request.method == "PATCH" and request.url.path.endswith(
            "/statefulsets/browserless"
        ):
            return httpx.Response(200, json={})
        if request.method == "DELETE" and request.url.path.endswith("/pods/browserless-1"):
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
    )

    await runtime.scale("browserless", 2, session_capacity=5)

    delete = next(request for request in requests if request.method == "DELETE")
    assert json.loads(delete.content) == {"preconditions": {"uid": "pod-1"}}
    patch = next(request for request in requests if request.method == "PATCH")
    assert json.loads(patch.content)["spec"]["updateStrategy"] == {"type": "OnDelete"}
    await runtime.close()


@pytest.mark.asyncio
async def test_kubernetes_runtime_does_not_roll_stale_pod_while_scaling() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/configmaps/workload"):
            return httpx.Response(200, json=_config_map())
        if request.url.path.endswith("/statefulsets/browserless"):
            return httpx.Response(200, json={"metadata": {"name": "browserless"}})
        if request.method == "GET" and request.url.path.endswith("/pods"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": "browserless-0",
                                "uid": "pod-0",
                                "ownerReferences": [
                                    {
                                        "apiVersion": "apps/v1",
                                        "kind": "StatefulSet",
                                        "name": "browserless",
                                        "controller": True,
                                    }
                                ],
                                "annotations": {
                                    "harbor.openai.com/workload-revision": "old",
                                    "harbor.openai.com/session-capacity": "5",
                                },
                            },
                            "status": {
                                "conditions": [{"type": "Ready", "status": "True"}]
                            },
                        }
                    ]
                },
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        base_url="https://kubernetes.test",
        transport=httpx.MockTransport(handler),
    )
    runtime = KubernetesRuntime(
        namespace="harbor",
        workload_config_map="workload",
        headless_service="browserless-headless",
        client=client,
    )

    await runtime.scale("browserless", 2, session_capacity=5)

    assert all(request.method != "DELETE" for request in requests)
    await runtime.close()
