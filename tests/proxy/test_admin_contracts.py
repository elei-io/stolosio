import tomllib
from pathlib import Path

from backend.api.main import app
from scripts.export_admin_contracts import DESTINATION, render


def test_admin_capture_contracts_match_backend():
    assert DESTINATION.read_text() == render(), (
        "Run uv run python -m scripts.export_admin_contracts after changing capture contracts"
    )


def test_api_version_matches_project_version():
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert app.version == tomllib.loads(project.read_text())["project"]["version"]
