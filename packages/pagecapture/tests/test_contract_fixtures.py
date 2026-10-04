"""The JSON examples in contract/ are the capture contract as clients (periplus) test against it.

They are built here from the contract types, so a contract change that forgets the examples fails this test.
Regenerate after an intended change: PAGECAPTURE_WRITE_CONTRACT=1 uv run pytest packages/pagecapture/tests
"""

import json
import os
from pathlib import Path

import pytest

from pagecapture import FAILURES, SCHEMA_VERSION, CaptureRequest, CaptureResult
from pagecapture.api import Assessment, Attempt, Cost, Document, Evidence, Failure, Reason, Redirect, Response
from pagecapture.failures import failure

CONTRACT = Path(__file__).resolve().parent.parent / "contract"
STARTED, FINISHED = "2026-09-30T09:12:03.120Z", "2026-09-30T09:12:27.201Z"
VERSIONS = {"pagecapture": "0.2.0", "classifier": "rules", "renderer": "adaptive-5"}
HTML_ONLY = ["text/html", "application/xhtml+xml", "application/xml", "text/xml"]

REQUEST = {
    "url": "https://news.example.test/articles/42",
    "resolve_bot_challenges": False,
    "exclusions": [{"host": "*.ads.example.test", "path_prefix": "/"}, {"host": "*", "path_prefix": "/wp-admin"}],
    "accept": HTML_ONLY,
    "reference": "3f0c7a52-6f7e-4d8e-9f64-0d1c2b6a9e11",
    "deadline_ms": 120000,
}


def captured() -> CaptureResult:
    html = b"<!DOCTYPE html><html><head><title>Article 42</title></head><body><main>...</main></body></html>"
    return CaptureResult(
        outcome="captured",
        requested_url=REQUEST["url"],
        final_url="https://news.example.test/articles/42/",
        started_at=STARTED,
        finished_at=FINISHED,
        reference=REQUEST["reference"],
        response=Response(
            200,
            [("content-type", "text/html; charset=utf-8"), ("set-cookie", "a=1"), ("set-cookie", "b=2")],
            [Redirect(301, REQUEST["url"], "/articles/42/")],
        ),
        document=Document("rendered_html", "text/html", "utf-8", html),
        evidence=Evidence(
            attempts=[
                Attempt(
                    "http",
                    "direct",
                    200,
                    612.0,
                    Assessment("app_shell", [Reason("app_shell", 0.9, "rule")], "empty", 0.9),
                    "escalate",
                    "app_shell: content missing without a browser",
                    reason_code="assessment",
                ),
                Attempt(
                    "browser",
                    "managed",
                    200,
                    23600.0,
                    Assessment(None, [], "complete", 0.9),
                    "accept",
                    "rendered content is complete (plain response had 12% of it)",
                    reason_code="content_comparison",
                    steps=[{"step": "parsed", "t": 0.9, "new_lines": 12, "new_items": 0}],
                    comparison={"http_coverage": 0.12, "http_sufficient": False},
                ),
            ],
            cost=Cost(browser_seconds=23.6, paid=False, bytes=2874983),
            versions=VERSIONS,
        ),
    )


def rate_limited() -> CaptureResult:
    body = b"<html><body><h1>Too Many Requests</h1></body></html>"
    return CaptureResult(
        outcome="failed",
        requested_url=REQUEST["url"],
        final_url=REQUEST["url"],
        started_at=STARTED,
        finished_at=FINISHED,
        reference=REQUEST["reference"],
        response=Response(429, [("content-type", "text/html"), ("retry-after", "120")]),
        document=Document("response_body", "text/html", None, body),
        failure=failure("rate_limited", "HTTP 429", retry_after_seconds=120.0),
        evidence=Evidence(
            attempts=[
                Attempt(
                    "http",
                    "direct",
                    429,
                    143.2,
                    Assessment("rate_limited", [Reason("rate_limited", 0.99, "rule")], None, 0.99),
                    "fail",
                    "rate_limited",
                    reason_code="assessment",
                )
            ],
            cost=Cost(bytes=len(body)),
            versions=VERSIONS,
        ),
    )


def unsupported_media_type() -> CaptureResult:
    return CaptureResult(
        outcome="failed",
        requested_url="https://news.example.test/report.pdf",
        final_url="https://news.example.test/report.pdf",
        started_at=STARTED,
        finished_at=FINISHED,
        reference=REQUEST["reference"],
        response=Response(200, [("content-type", "application/pdf"), ("content-length", "482113")]),
        failure=Failure(
            "unsupported_media_type", "content", False, "media type application/pdf is not accepted", None, None
        ),
        evidence=Evidence(
            attempts=[
                Attempt(
                    "http",
                    "direct",
                    200,
                    88.4,
                    Assessment(None),
                    "fail",
                    "media type application/pdf not accepted",
                    reason_code="media_type",
                )
            ],
            versions=VERSIONS,
        ),
    )


def excluded() -> CaptureResult:
    return CaptureResult(
        outcome="failed",
        requested_url="https://news.example.test/login",
        final_url="https://news.example.test/login",
        started_at=STARTED,
        finished_at=FINISHED,
        reference=REQUEST["reference"],
        failure=failure("excluded", "redirected to an excluded URL: https://news.example.test/wp-admin/"),
        evidence=Evidence(
            attempts=[
                Attempt(
                    "http",
                    "direct",
                    None,
                    51.0,
                    Assessment(None),
                    "fail",
                    "redirected to an excluded URL: https://news.example.test/wp-admin/",
                )
            ],
            versions=VERSIONS,
        ),
    )


def locally_resolved() -> CaptureResult:
    result = captured()
    first, local = result.evidence.attempts
    first.assessment = Assessment("bot_challenge", [Reason("bot_challenge", 0.95, "rule")])
    first.decision_reason = "bot challenge: trying local_resolution"
    local.tier = "local_resolution"
    local.duration_ms = 2000.0
    local.decision_reason = "rendered content is complete (plain response had 0% of it)"
    local.comparison = {"http_coverage": 0.0, "http_sufficient": False}
    result.evidence.cost.browser_seconds = 2.0
    return result


EXAMPLES = {
    "request.json": REQUEST,
    "captured.json": captured().to_json(),
    "captured_local_resolution.json": locally_resolved().to_json(),
    "failed_rate_limited.json": rate_limited().to_json(),
    "failed_unsupported_media_type.json": unsupported_media_type().to_json(),
    "failed_excluded.json": excluded().to_json(),
    "failure_codes.json": {code: {"category": c, "transient": t} for code, (c, t) in FAILURES.items()},
}


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_contract_examples_match_the_contract_types(name):
    path = CONTRACT / name
    text = json.dumps(EXAMPLES[name], indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("PAGECAPTURE_WRITE_CONTRACT"):
        CONTRACT.mkdir(exist_ok=True)
        path.write_text(text)
    assert path.read_text() == text, f"{name} is stale: regenerate it (see this module's docstring)"


def test_the_example_request_is_valid_and_results_carry_the_schema_version():
    assert CaptureRequest.from_json(REQUEST).accept == tuple(HTML_ONLY)
    assert SCHEMA_VERSION == "2"
    assert all(example.to_json()["schema"] == "2" for example in (captured(), rate_limited(), excluded()))
