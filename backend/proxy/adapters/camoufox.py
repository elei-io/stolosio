import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import (
    Browser,
    BrowserContext,
    JSHandle,
    Page,
    Playwright,
    async_playwright,
)

from backend.proxy.contracts import HarborSession, ProviderName, ResolvedSessionSettings

_CLOSED = object()


class CamoufoxAdapter:
    provider = ProviderName.CAMOUFOX

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ) -> "CamoufoxProviderSession":
        playwright = await async_playwright().start()
        try:
            browser = await playwright.firefox.connect(self._endpoint)
            context = await browser.new_context()
        except Exception:
            await playwright.stop()
            raise
        return CamoufoxProviderSession(playwright, browser, context)


class CamoufoxProviderSession:
    provider = ProviderName.CAMOUFOX

    def __init__(
        self,
        playwright: Playwright,
        browser: Browser,
        context: BrowserContext,
    ) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page: Page | None = None
        self._messages: asyncio.Queue[str | object] = asyncio.Queue()
        self._closed = False
        self._target_id = uuid4().hex.upper()
        self._cdp_session_id = uuid4().hex.upper()
        self._context_id = uuid4().hex.upper()
        self._frame_id = self._target_id
        self._loader_id = uuid4().hex.upper()
        self._execution_context_id = 1
        self._utility_context_id = 2
        self._utility_world_name = "__playwright_utility_world"
        self._handles: dict[str, JSHandle] = {}

    async def send(self, message: str) -> None:
        command = json.loads(message)
        command_id = command["id"]
        method = command["method"]
        params = command.get("params", {})
        session_id = command.get("sessionId")
        try:
            result = await self._dispatch(method, params, session_id)
        except Exception as error:
            await self._put(
                {
                    "id": command_id,
                    "error": {"code": -32000, "message": str(error)},
                    **self._session_field(session_id),
                }
            )
            return
        await self._put(
            {
                "id": command_id,
                "result": result,
                **self._session_field(session_id),
            }
        )

    async def messages(self) -> AsyncIterator[str]:
        while True:
            message = await self._messages.get()
            if message is _CLOSED:
                return
            yield str(message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for handle in self._handles.values():
            with suppress(Exception):
                await handle.dispose()
        with suppress(Exception):
            await self._context.close()
        with suppress(Exception):
            await self._browser.close()
        with suppress(Exception):
            await self._playwright.stop()
        await self._messages.put(_CLOSED)

    async def _dispatch(
        self,
        method: str,
        params: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        if method == "Browser.getVersion":
            return {
                "protocolVersion": "1.3",
                "product": "Harbor/Camoufox",
                "revision": "camoufox",
                "userAgent": "Mozilla/5.0 Firefox/Camoufox",
                "jsVersion": "SpiderMonkey",
            }
        if method in {
            "Browser.setDownloadBehavior",
            "Browser.setWindowBounds",
            "Emulation.setDeviceMetricsOverride",
            "Emulation.setEmulatedMedia",
            "Emulation.setFocusEmulationEnabled",
            "Log.enable",
            "Network.enable",
            "Page.enable",
            "Page.setLifecycleEventsEnabled",
            "Target.setAutoAttach",
            "Runtime.runIfWaitingForDebugger",
        }:
            return {}
        if method == "Target.getTargetInfo":
            return {"targetInfo": self._target_info()}
        if method == "Target.createBrowserContext":
            return {"browserContextId": self._context_id}
        if method == "Target.createTarget":
            await self._create_page(params.get("url", "about:blank"))
            await self._event(
                "Target.attachedToTarget",
                {
                    "sessionId": self._cdp_session_id,
                    "targetInfo": self._target_info(),
                    "waitingForDebugger": True,
                },
                None,
            )
            return {"targetId": self._target_id}
        if method == "Browser.getWindowForTarget":
            return {
                "windowId": 1,
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": 1280,
                    "height": 720,
                    "windowState": "normal",
                },
            }
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": self._frame()}}
        if method == "Runtime.enable":
            await self._emit_execution_context(session_id, self._execution_context_id, "", True)
            return {}
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self._utility_world_name = params.get("worldName", self._utility_world_name)
            return {"identifier": "1"}
        if method == "Page.createIsolatedWorld":
            self._utility_world_name = params.get("worldName", self._utility_world_name)
            self._utility_context_id += 1
            await self._emit_execution_context(
                session_id,
                self._utility_context_id,
                params.get("worldName", "__playwright_utility_world"),
                False,
            )
            return {"executionContextId": self._utility_context_id}
        if method == "Page.navigate":
            return await self._navigate(params["url"], session_id)
        if method == "Runtime.evaluate":
            return await self._runtime_evaluate(params)
        if method == "Runtime.callFunctionOn":
            return await self._runtime_call_function(params)
        if method == "Runtime.releaseObject":
            handle = self._handles.pop(params["objectId"], None)
            if handle is not None:
                await handle.dispose()
            return {}
        if method == "DOM.scrollIntoViewIfNeeded":
            await self._handle(params["objectId"]).scroll_into_view_if_needed()
            return {}
        if method == "DOM.getContentQuads":
            box = await self._handle(params["objectId"]).bounding_box()
            if box is None:
                return {"quads": []}
            x, y, width, height = box["x"], box["y"], box["width"], box["height"]
            return {"quads": [[x, y, x + width, y, x + width, y + height, x, y + height]]}
        if method == "Input.dispatchMouseEvent":
            return await self._dispatch_mouse(params, session_id)
        raise RuntimeError(f"CDP method {method} has no Camoufox mapping")

    async def _create_page(self, url: str) -> None:
        if self._page is None:
            self._page = await self._context.new_page()
        if url and url != "about:blank":
            await self._page.goto(url)

    async def _navigate(self, url: str, session_id: str | None) -> dict[str, Any]:
        page = self._require_page()
        self._loader_id = uuid4().hex.upper()
        request_id = self._loader_id
        await self._event(
            "Page.frameStartedNavigating",
            {
                "frameId": self._frame_id,
                "url": url,
                "loaderId": self._loader_id,
                "navigationType": "differentDocument",
            },
            session_id,
        )
        await self._event("Page.frameStartedLoading", {"frameId": self._frame_id}, session_id)
        await self._event(
            "Network.requestWillBeSent",
            {
                "requestId": request_id,
                "loaderId": self._loader_id,
                "documentURL": url,
                "request": {
                    "url": url,
                    "method": "GET",
                    "headers": {},
                    "initialPriority": "VeryHigh",
                    "referrerPolicy": "no-referrer-when-downgrade",
                },
                "timestamp": time.monotonic(),
                "wallTime": time.time(),
                "initiator": {"type": "other"},
                "type": "Document",
                "frameId": self._frame_id,
                "hasUserGesture": False,
            },
            session_id,
        )
        response = await page.goto(url)
        status = response.status if response is not None else 200
        headers = await response.all_headers() if response is not None else {}
        final_url = page.url
        await self._event(
            "Network.responseReceived",
            {
                "requestId": request_id,
                "loaderId": self._loader_id,
                "timestamp": time.monotonic(),
                "type": "Document",
                "response": {
                    "url": final_url,
                    "status": status,
                    "statusText": "",
                    "headers": headers,
                    "mimeType": headers.get("content-type", "text/html").split(";")[0],
                    "connectionReused": False,
                    "connectionId": 0,
                    "encodedDataLength": 0,
                    "securityState": "secure" if final_url.startswith("https:") else "neutral",
                    "fromDiskCache": False,
                    "fromServiceWorker": False,
                    "timing": {
                        "requestTime": time.monotonic(),
                        "proxyStart": -1,
                        "proxyEnd": -1,
                        "dnsStart": -1,
                        "dnsEnd": -1,
                        "connectStart": -1,
                        "connectEnd": -1,
                        "sslStart": -1,
                        "sslEnd": -1,
                        "workerStart": -1,
                        "workerReady": -1,
                        "workerFetchStart": -1,
                        "workerRespondWithSettled": -1,
                        "sendStart": 0,
                        "sendEnd": 0,
                        "pushStart": 0,
                        "pushEnd": 0,
                        "receiveHeadersStart": 0,
                        "receiveHeadersEnd": 0,
                    },
                },
                "hasExtraInfo": False,
                "frameId": self._frame_id,
            },
            session_id,
        )
        await self._emit_loaded_document(session_id, request_id)
        return {"frameId": self._frame_id, "loaderId": self._loader_id, "isDownload": False}

    async def _emit_loaded_document(
        self,
        session_id: str | None,
        request_id: str,
    ) -> None:
        timestamp = time.monotonic()
        await self._event("Runtime.executionContextsCleared", {}, session_id)
        await self._event(
            "Page.frameNavigated",
            {"frame": self._frame(), "type": "Navigation"},
            session_id,
        )
        self._execution_context_id += 2
        self._utility_context_id = self._execution_context_id + 1
        await self._emit_execution_context(session_id, self._execution_context_id, "", True)
        await self._emit_execution_context(
            session_id,
            self._utility_context_id,
            self._utility_world_name,
            False,
        )
        await self._event(
            "Network.loadingFinished",
            {"requestId": request_id, "timestamp": timestamp, "encodedDataLength": 0},
            session_id,
        )
        await self._event("Page.domContentEventFired", {"timestamp": timestamp}, session_id)
        await self._event(
            "Page.lifecycleEvent",
            {
                "frameId": self._frame_id,
                "loaderId": self._loader_id,
                "name": "DOMContentLoaded",
                "timestamp": timestamp,
            },
            session_id,
        )
        await self._event("Page.loadEventFired", {"timestamp": timestamp}, session_id)
        await self._event(
            "Page.lifecycleEvent",
            {
                "frameId": self._frame_id,
                "loaderId": self._loader_id,
                "name": "load",
                "timestamp": timestamp,
            },
            session_id,
        )
        await self._event("Page.frameStoppedLoading", {"frameId": self._frame_id}, session_id)

    async def _runtime_evaluate(self, params: dict[str, Any]) -> dict[str, Any]:
        page = self._require_page()
        expression = params["expression"]
        if params.get("returnByValue"):
            value = await page.evaluate(expression)
            return {"result": self._remote_value(value)}
        handle = await page.evaluate_handle(expression)
        return {"result": await self._store_handle(handle)}

    async def _runtime_call_function(self, params: dict[str, Any]) -> dict[str, Any]:
        receiver = self._handle(params["objectId"])
        declaration = params["functionDeclaration"]
        arguments = [self._decode_argument(argument) for argument in params.get("arguments", [])]
        payload = {"declaration": declaration, "arguments": arguments}
        wrapper = """(receiver, payload) => {
            const fn = globalThis.eval('(' + payload.declaration + ')');
            return fn.apply(receiver, payload.arguments);
        }"""
        if params.get("returnByValue"):
            value = await receiver.evaluate(wrapper, payload)
            return {"result": self._remote_value(value)}
        handle = await receiver.evaluate_handle(wrapper, payload)
        return {"result": await self._store_handle(handle)}

    async def _dispatch_mouse(
        self,
        params: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        page = self._require_page()
        event_type = params["type"]
        x = params.get("x", 0)
        y = params.get("y", 0)
        button = params.get("button", "left")
        if event_type == "mouseMoved":
            await page.mouse.move(x, y)
        elif event_type == "mousePressed":
            await page.mouse.down(button=button)
        elif event_type == "mouseReleased":
            before = page.url
            await page.mouse.up(button=button)
            deadline = time.monotonic() + 5
            while page.url == before and time.monotonic() < deadline:
                await page.wait_for_timeout(10)
            with suppress(Exception):
                await page.wait_for_load_state("load", timeout=5_000)
            if page.url != before:
                self._loader_id = uuid4().hex.upper()
                await self._emit_loaded_document(session_id, self._loader_id)
        return {}

    async def _store_handle(self, handle: JSHandle) -> dict[str, Any]:
        object_id = uuid4().hex
        self._handles[object_id] = handle
        description = await handle.evaluate(
            "value => Object.prototype.toString.call(value).slice(8, -1)"
        )
        subtype = "node" if description in {"HTMLHtmlElement", "HTMLAnchorElement"} else None
        result: dict[str, Any] = {
            "type": "function" if description in {"Function", "AsyncFunction"} else "object",
            "className": description,
            "description": description,
            "objectId": object_id,
        }
        if subtype:
            result["subtype"] = subtype
        return result

    def _decode_argument(self, argument: dict[str, Any]) -> Any:
        if "objectId" in argument:
            return self._handle(argument["objectId"])
        if "value" in argument:
            return argument["value"]
        value = argument.get("unserializableValue")
        return {
            "undefined": None,
            "NaN": float("nan"),
            "Infinity": float("inf"),
            "-Infinity": float("-inf"),
            "-0": -0.0,
        }.get(value, value)

    @staticmethod
    def _remote_value(value: Any) -> dict[str, Any]:
        if value is None:
            return {"type": "object", "subtype": "null", "value": None}
        if isinstance(value, bool):
            return {"type": "boolean", "value": value}
        if isinstance(value, (int, float)):
            return {"type": "number", "value": value}
        if isinstance(value, str):
            return {"type": "string", "value": value}
        return {"type": "object", "value": value}

    def _handle(self, object_id: str) -> JSHandle:
        try:
            return self._handles[object_id]
        except KeyError as error:
            raise RuntimeError(f"Unknown Camoufox remote object {object_id}") from error

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Camoufox page target has not been created")
        return self._page

    def _target_info(self) -> dict[str, Any]:
        return {
            "targetId": self._target_id,
            "type": "page",
            "title": "",
            "url": self._page.url if self._page is not None else "about:blank",
            "attached": True,
            "canAccessOpener": False,
            "browserContextId": self._context_id,
        }

    def _frame(self) -> dict[str, Any]:
        url = self._page.url if self._page is not None else "about:blank"
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else "://"
        return {
            "id": self._frame_id,
            "loaderId": self._loader_id,
            "url": url,
            "domainAndRegistry": parsed.hostname or "",
            "securityOrigin": origin,
            "mimeType": "text/html",
            "secureContextType": "Secure" if parsed.scheme == "https" else "InsecureScheme",
        }

    async def _emit_execution_context(
        self,
        session_id: str | None,
        context_id: int,
        name: str,
        is_default: bool,
    ) -> None:
        await self._event(
            "Runtime.executionContextCreated",
            {
                "context": {
                    "id": context_id,
                    "origin": self._frame()["securityOrigin"],
                    "name": name,
                    "uniqueId": f"harbor-{self._target_id}-{context_id}",
                    "auxData": {
                        "isDefault": is_default,
                        "type": "default" if is_default else "isolated",
                        "frameId": self._frame_id,
                    },
                }
            },
            session_id,
        )

    async def _event(
        self,
        method: str,
        params: dict[str, Any],
        session_id: str | None,
    ) -> None:
        await self._put({"method": method, "params": params, **self._session_field(session_id)})

    async def _put(self, message: dict[str, Any]) -> None:
        await self._messages.put(json.dumps(message, separators=(",", ":")))

    @staticmethod
    def _session_field(session_id: str | None) -> dict[str, str]:
        return {"sessionId": session_id} if session_id else {}
