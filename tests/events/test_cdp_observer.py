import hashlib
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
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.CHROMIUM, publisher)
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

    assert [event.event_type for event in publisher.events] == [
        "navigation.requested",
        "command.received",
        "command.succeeded",
        "navigation.response",
    ]
    assert publisher.events[1].payload["domain"] == "example.com"
    assert publisher.events[-1].payload == {
        "url": "https://example.com/path",
        "status": 200,
        "mime_type": "text/html",
        "resource_type": "Document",
        "selected_headers": {"content-type": "text/html"},
    }
    serialized = b"".join(event.to_json() for event in publisher.events)
    assert b"token=secret" not in serialized
    assert b"Set-Cookie" not in serialized
    assert b"secret-frame" not in serialized


@pytest.mark.asyncio
async def test_unsupported_and_interrupted_commands_get_terminal_events() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.LIGHTPANDA, publisher)
    await observer.command_received({"id": 1, "method": "Page.printToPDF"})
    await observer.command_unsupported(1)
    await observer.command_received({"id": 2, "method": "Runtime.evaluate"})
    await observer.interrupt_pending("session_ended")

    assert [event.event_type for event in publisher.events] == [
        "command.received",
        "command.failed",
        "command.received",
        "command.interrupted",
    ]
    assert publisher.events[1].payload["cdp_error_code"] == -32601
    assert publisher.events[-1].payload["reason"] == "session_ended"


@pytest.mark.asyncio
async def test_malformed_provider_evidence_does_not_escape_the_observer() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.CHROMIUM, publisher)

    await observer.command_received({"id": 1, "method": "Runtime.enable", "params": ["unexpected"]})
    await observer.upstream_message("[]")
    await observer.upstream_message(
        '{"method":"Network.responseReceived","params":'
        '{"type":"Document","response":{"url":"https://example.com/",'
        '"status":"not-a-number","headers":{"Content-Length":42}}}}'
    )

    assert [event.event_type for event in publisher.events] == [
        "command.received",
        "navigation.response",
    ]
    assert "status" not in publisher.events[-1].payload


@pytest.mark.asyncio
async def test_content_and_console_are_recorded_as_fingerprints_only() -> None:
    publisher = CapturingPublisher()
    observer = CdpEventObserver(uuid4(), uuid4(), ProviderName.CHROMIUM, publisher)
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
    assert content.payload == {
        "content_fingerprint": hashlib.sha256(html.encode()).hexdigest(),
        "content_length": len(html),
    }
    serialized = b"".join(event.to_json() for event in publisher.events)
    assert b"private content" not in serialized
    assert b"private console text" not in serialized
    assert len(console.payload["message_fingerprint"]) == 64
