from pagecapture.api import Assessment, Attempt, CaptureResult, Evidence

from backend.events.registry import EventType, validate_payload
from backend.proxy.capture.service import capture_attempts, capture_hostname


def test_capture_observations_do_not_publish_free_text_or_cache_keys():
    secret = "https://user:password@example.test/path?token=secret#private"
    result = CaptureResult(
        "captured",
        secret,
        secret,
        "start",
        "end",
        evidence=Evidence(
            attempts=[
                Attempt(
                    "http",
                    "direct",
                    200,
                    12,
                    Assessment(None),
                    "accept",
                    f"cache: pattern {secret} HTTP-sufficient",
                    reason_code="cache_pattern",
                    notes=[secret],
                    steps=[{"url": secret}],
                ),
                Attempt(
                    "browser",
                    "managed",
                    200,
                    30,
                    Assessment(secret),
                    "accept",
                    secret,
                    comparison={"http_coverage": 0.99, "http_sufficient": True},
                ),
            ]
        ),
    )
    attempts = capture_attempts(result)
    payload = validate_payload(
        EventType.CAPTURE_COMPLETED,
        {
            "outcome": "captured",
            "tiers": ["direct", "managed"],
            "duration_ms": 42,
            "browser_seconds": 0.03,
            "paid": False,
            "bytes": 300,
            "attempts": attempts,
        },
    )
    assert attempts[0]["reason"] == "cache_pattern"
    result.evidence.attempts[0].decision_reason = "Entirely different explanatory wording"
    assert capture_attempts(result)[0]["reason"] == "cache_pattern"
    assert attempts[1]["assessment"] is None
    assert attempts[1]["http_sufficient"] is True
    assert "secret" not in str(payload)
    assert "example.test" not in str(payload)
    assert "notes" not in str(payload)
    assert "steps" not in str(payload)
    assert capture_hostname(secret) == "example.test"
    assert capture_hostname("https://" + "x" * 300 + ".test/") is None
