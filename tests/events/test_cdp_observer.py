import json
from uuid import uuid4

import pytest

from backend.events.cdp import CdpEventObserver
from backend.proxy.contracts import ProviderName


class CapturingPublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_command_and_navigation_events_are_correlated_and_sanitized() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.BROWSERLESS, publisher)
    await observer.command_received(
        {
            "id": 7,
            "method": "Page.navigate",
            "params": {"url": "https://user:pass@example.com/path?token=secret#fragment"},
        }
    )
    await observer.upstream_message('{"id":7,"result":{"frameId":"secret-frame"}}')
    await observer.upstream_message(
        json.dumps(
            {
                "method": "Network.responseReceived",
                "params": {
                    "type": "Document",
                    "response": {
                        "url": "https://example.com/path?token=secret",
                        "status": 200,
                        "mimeType": "text/html",
                        "headers": {
                            "Content-Type": "text/html",
                            "Set-Cookie": "secret=value",
                        },
                    },
                },
            }
        )
    )
    await observer.flush_command_summaries()

    assert [event.event_type for event in publisher.events] == [
        "navigation.requested",
        "navigation.response",
        "command.summary",
    ]
    assert publisher.events[1].payload == {
        "url": "https://example.com/path",
        "status": 200,
        "mime_type": "text/html",
        "resource_type": "Document",
        "selected_headers": {"content-type": "text/html"},
    }
    assert publisher.events[-1].payload["methods"]["Page.navigate"]["count"] == 1
    serialized = b"".join(event.to_json() for event in publisher.events)
    assert b"token=secret" not in serialized
    assert b"Set-Cookie" not in serialized
    assert b"secret-frame" not in serialized


@pytest.mark.asyncio
async def test_unsupported_and_interrupted_commands_get_terminal_events() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.BROWSERBASE, publisher)
    await observer.command_received({"id": 1, "method": "Page.printToPDF"})
    await observer.command_unsupported(1)
    await observer.command_received({"id": 2, "method": "Runtime.evaluate"})
    await observer.interrupt_pending("session_ended")
    await observer.flush_command_summaries()

    assert [event.event_type for event in publisher.events] == [
        "command.failed",
        "command.interrupted",
        "command.summary",
    ]
    assert publisher.events[0].payload["cdp_error_code"] == -32601
    assert publisher.events[1].payload["reason"] == "session_ended"
    assert publisher.events[-1].payload["methods"]["Page.printToPDF"]["failed_count"] == 1
    assert publisher.events[-1].payload["methods"]["Runtime.evaluate"]["interrupted_count"] == 1


@pytest.mark.asyncio
async def test_attempt_phase_summary_measures_command_union_without_retaining_commands(
) -> None:
    observed_times = iter((0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0))
    publisher = CapturingPublisher()
    attempt_id = uuid4()
    observer = CdpEventObserver(
        uuid4(),
        attempt_id,
        ProviderName.BROWSERLESS,
        publisher,
        clock=lambda: next(observed_times),
    )

    await observer.command_received({"id": 1, "method": "Runtime.evaluate"})
    observer.command_forwarded({"id": 1, "method": "Runtime.evaluate"})
    await observer.command_received({"id": 2, "method": "Runtime.callFunctionOn"})
    observer.command_forwarded({"id": 2, "method": "Runtime.callFunctionOn"})
    await observer.upstream_message('{"id":1,"result":{}}')
    await observer.upstream_message('{"id":2,"result":{}}')

    assert observer.phase_summary(attempt_id) == {
        "measurement_version": 1,
        "observed_session_ms": 10_000,
        "command_active_ms": 6_000,
        "no_command_in_flight_ms": 4_000,
        "pre_first_command_ms": 2_000,
        "post_last_command_ms": 2_000,
        "transition_replay_ms": 0,
        "provider_bootstrap_ms": 0,
        "provider_close_ms": 0,
    }


@pytest.mark.asyncio
async def test_attempt_phase_summary_moves_pending_command_to_transition_target(
) -> None:
    observed_times = iter((0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 7.0))
    publisher = CapturingPublisher()
    source_attempt_id = uuid4()
    target_attempt_id = uuid4()
    observer = CdpEventObserver(
        uuid4(),
        source_attempt_id,
        ProviderName.HTTP,
        publisher,
        clock=lambda: next(observed_times),
    )

    await observer.command_received({"id": 1, "method": "Runtime.callFunctionOn"})
    observer.command_forwarded({"id": 1, "method": "Runtime.callFunctionOn"})
    observer.start_attempt_phase(ProviderName.BROWSERLESS, target_attempt_id)
    observer.record_attempt_phase(target_attempt_id, "transition_replay_ms", 1_000)
    observer.bind_attempt(ProviderName.BROWSERLESS, target_attempt_id)
    await observer.upstream_message('{"id":1,"result":{}}')

    target = observer.phase_summary(target_attempt_id)
    assert target is not None
    assert target["observed_session_ms"] == 4_000
    assert target["command_active_ms"] == 2_000
    assert target["transition_replay_ms"] == 1_000


@pytest.mark.asyncio
async def test_provider_disconnect_records_the_stable_reason() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.BROWSERLESS, publisher)

    await observer.provider_disconnected("domain_blocking_unavailable")

    assert publisher.events[-1].event_type == "provider.disconnected"
    assert publisher.events[-1].payload == {
        "reason": "domain_blocking_unavailable",
    }


@pytest.mark.asyncio
async def test_malformed_provider_evidence_does_not_escape_the_observer() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.BROWSERLESS, publisher)

    await observer.command_received({"id": 1, "method": "Runtime.enable", "params": ["unexpected"]})
    await observer.upstream_message("[]")
    await observer.upstream_message(
        '{"method":"Network.responseReceived","params":'
        '{"type":"Document","response":{"url":"https://example.com/",'
        '"status":"not-a-number","headers":{"Content-Length":42}}}}'
    )

    assert [event.event_type for event in publisher.events] == [
        "navigation.response",
    ]
    assert "status" not in publisher.events[-1].payload


@pytest.mark.asyncio
async def test_content_length_and_console_fingerprint_exclude_sensitive_text() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.BROWSERLESS, publisher)
    expression = """() => {
        let retVal = "";
        if (document.doctype)
          retVal = new XMLSerializer().serializeToString(document.doctype);
        if (document.documentElement)
          retVal += document.documentElement.outerHTML;
        return retVal;
      }"""
    html = "<html><body>private content</body></html>"
    await observer.command_received(
        {
            "id": 1,
            "method": "Runtime.callFunctionOn",
            "params": {
                "arguments": [{}, {}, {}, {"value": expression}],
                "returnByValue": True,
            },
        }
    )
    await observer.upstream_message(
        json.dumps(
            {
                "id": 1,
                "result": {"result": {"type": "string", "value": html}},
            }
        )
    )
    await observer.upstream_message(
        json.dumps(
            {
                "method": "Runtime.consoleAPICalled",
                "params": {"type": "error", "args": [{"value": "private console text"}]},
            }
        )
    )

    content = next(
        event for event in publisher.events if event.event_type == "page.content_observed"
    )
    console = next(event for event in publisher.events if event.event_type == "console.message")
    assert content.payload == {"content_length": len(html)}
    serialized = b"".join(event.to_json() for event in publisher.events)
    assert b"private content" not in serialized
    assert b"private console text" not in serialized
    assert len(console.payload["message_fingerprint"]) == 64
