import base64
import json
import os
from uuid import uuid4

import httpx
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("STOLOSIO_E2E") != "1",
        reason="set STOLOSIO_E2E=1 with the Docker Compose stack running",
    ),
]
API = os.getenv("STOLOSIO_E2E_HTTP_URL", "http://localhost:8411")
HTML_ONLY = ["text/html", "application/xhtml+xml", "application/xml", "text/xml"]


def capture(**body) -> dict:
    response = httpx.post(f"{API}/v1/capture", json=body, timeout=180)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_page_is_captured_through_the_egress_proxy_and_the_local_fleet() -> None:
    result = capture(url="https://example.com/", accept=HTML_ONLY, reference="e2e-capture")

    assert result["outcome"] == "captured", json.dumps(result)["failure"]
    assert result["reference"] == "e2e-capture"
    assert result["document"]["media_type"] == "text/html"
    assert [a["tier"] for a in result["evidence"]["attempts"]][:1] == ["direct"]


def test_a_redirect_into_an_exclusion_fails_as_excluded() -> None:
    result = capture(url="http://www.github.com/", exclusions=[{"host": "github.com"}])

    assert result["outcome"] == "failed"
    assert result["failure"]["code"] == "excluded" and result["document"] is None


def test_an_unaccepted_media_type_fails_without_a_body() -> None:
    result = capture(
        url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        accept=HTML_ONLY,
    )

    assert result["failure"]["code"] == "unsupported_media_type"
    assert result["document"] is None


@pytest.mark.parametrize("allow_resolution", [False, True])
def test_local_resolution_clears_a_browser_challenge_without_paid_fallback(
    local_challenge_site, allow_resolution
):
    origin = local_challenge_site
    before = httpx.get(f"{API}/v1/admin/captures/overview").raise_for_status().json()
    result = capture(url=f"{origin}/clears/{uuid4().hex}", resolve_bot_challenges=allow_resolution)
    if not allow_resolution:
        assert result["outcome"] == "failed"
        assert not result["failure"]["resolution_attempted"]
        assert [a["tier"] for a in result["evidence"]["attempts"]] == ["direct"]
        return
    assert result["outcome"] == "captured", json.dumps(result)
    assert [a["tier"] for a in result["evidence"]["attempts"]] == ["direct", "local_resolution"]
    assert not result["evidence"]["cost"]["paid"]
    after = httpx.get(f"{API}/v1/admin/captures/overview").raise_for_status().json()
    for cohort in ("all_captures", "challenged_opt_in"):
        assert (
            after[cohort]["counts"]["internally_resolved"]
            >= before[cohort]["counts"]["internally_resolved"] + 1
        )
    assert result["evidence"]["cost"]["browser_seconds"] > 0
    assert result["evidence"]["attempts"][-1]["decision"] == "accept"
    body = base64.b64decode(result["document"]["body_base64"]).decode()
    assert "Local resolution demonstration" in body and "Section 11" in body
    print(
        json.dumps(
            {
                "outcome": result["outcome"],
                "allow_resolution": allow_resolution,
                "tiers": [a["tier"] for a in result["evidence"]["attempts"]],
                "cost": result["evidence"]["cost"],
            }
        )
    )


def test_challenge_resolution_is_skipped_without_permission(
    local_challenge_site,
):
    origin = local_challenge_site
    result = capture(url=f"{origin}/holds/{uuid4().hex}")
    assert result["outcome"] == "failed", result
    assert result["failure"]["code"] == "bot_challenge", json.dumps(result)
    assert not result["failure"]["resolution_attempted"]
    assert [a["tier"] for a in result["evidence"]["attempts"]] == ["direct"]
    assert not result["evidence"]["cost"]["paid"]


@pytest.mark.parametrize("path", ["resources", "progress"])
def test_local_resolution_allows_resources_and_progress(local_challenge_site, path):
    result = capture(
        url=f"{local_challenge_site}/{path}/{uuid4().hex}", resolve_bot_challenges=True
    )
    assert result["outcome"] == "captured", json.dumps(result)
    attempt = result["evidence"]["attempts"][-1]
    assert attempt["tier"] == "local_resolution" and attempt["decision"] == "accept"
    assert not result["evidence"]["cost"]["paid"]
    assert "Section 11" in base64.b64decode(result["document"]["body_base64"]).decode()
    if path == "progress":
        assert attempt["duration_ms"] > 10000
