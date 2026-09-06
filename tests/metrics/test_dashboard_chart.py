"""Chart contract checks; requires Helm (also exercised in the Helm CI job)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")


def render(*extra):
    return subprocess.check_output(
        [
            "helm",
            "template",
            "demo",
            str(ROOT / "charts/stolosio"),
            "--namespace",
            "demo-space",
            "--set",
            "database.existingSecret=test",
            "--set",
            "nats.existingSecret=test",
            *extra,
        ],
        text=True,
    )


def test_monitoring_resources_are_opt_in():
    result = render()
    assert "kind: PrometheusRule" not in result
    assert "grafana_dashboard" not in result


def test_dashboard_is_valid_scoped_json_with_literal_grafana_templates():
    result = render(
        "--set",
        "monitoring.enabled=true",
        "--set",
        "monitoring.dashboard.enabled=true",
        "--set",
        "monitoring.dashboard.adminUrl=https://admin.example.test",
        "--show-only",
        "templates/dashboard.yaml",
    )
    dashboard = json.loads(result.split(".json: |\n", 1)[1])
    variables = {item["name"]: item for item in dashboard["templating"]["list"]}
    assert variables["namespace"]["current"]["value"] == "demo-space"
    assert variables["release"]["current"]["value"] == "demo-stolosio"
    assert dashboard["links"][0]["url"] == "https://admin.example.test"
    assert len(dashboard["uid"]) <= 40
    assert "__NAMESPACE__" not in result
    assert "{{provider}}" in result
    panels = dashboard["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    queries = [target["expr"] for panel in panels for target in panel.get("targets", [])]
    assert all("$namespace" in query for query in queries)
    assert all(
        "max by (provider)" in query
        for query in queries
        if "stolosio_provider_ready_instances" in query
    )
