import asyncio
from collections.abc import Awaitable, Callable

from pagecapture import BqlBrowserTier, Exclusion
from pagecapture.render import Rendered, Renderer


class SharedRenderer:
    """One Playwright driver for every capture on this process, started on first use."""

    def __init__(self, renderer: Renderer) -> None:
        self._renderer = renderer
        self._started = False
        self._lock = asyncio.Lock()

    async def render(
        self, url: str, deadline_s: float, exclusions: tuple[Exclusion, ...], endpoint: str
    ) -> Rendered:
        if not self._started:
            async with self._lock:
                if not self._started:
                    await self._renderer.__aenter__()
                    self._started = True
        return await self._renderer.render(
            url, deadline_s=deadline_s, exclusions=exclusions, endpoint=endpoint
        )

    async def close(self) -> None:
        if self._started:
            await self._renderer.__aexit__(None, None, None)
            self._started = False


class SlotTier:
    """Acquire a local slot on first render; managed and local tiers reuse its endpoint."""

    tier = "managed"
    paid = False
    proxied = False

    def __init__(
        self,
        renderer: SharedRenderer,
        endpoint: Callable[[float], Awaitable[str]],
        *,
        local: bool = False,
    ) -> None:
        self._renderer = renderer
        self._endpoint = endpoint
        self.tier = "local_resolution" if local else "managed"

    async def render(
        self, url: str, deadline_s: float, exclusions: tuple[Exclusion, ...] = ()
    ) -> Rendered:
        started = asyncio.get_running_loop().time()
        endpoint = await self._endpoint(deadline_s)
        remaining = deadline_s - (asyncio.get_running_loop().time() - started)
        if remaining <= 0:
            raise TimeoutError("capture deadline expired while acquiring browser capacity")
        return await self._renderer.render(url, remaining, exclusions, endpoint)


class CloudChallengeTier:
    """The challenge tier: Browserless cloud (BrowserQL through a residential proxy). `switch`
    trades the capture's local slot for a browserless_cloud attempt first, so paid concurrency is
    bounded like any provider's; it raises pagecapture's BrowserCapacity when none is free."""

    tier = "challenge_resolution"
    paid = True
    proxied = True

    def __init__(self, bql: BqlBrowserTier, switch: Callable[[float], Awaitable[None]]) -> None:
        self._bql = bql
        self._switch = switch

    async def render(
        self, url: str, deadline_s: float, exclusions: tuple[Exclusion, ...] = ()
    ) -> Rendered:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await self._switch(deadline_s)
        return await self._bql.render(url, deadline_s - (loop.time() - started), exclusions)
