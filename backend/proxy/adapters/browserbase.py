import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from websockets.asyncio.client import connect

from backend.proxy.adapters.cdp import WebSocketProviderSession
from backend.proxy.contracts import HarborSession, ProviderName, ResolvedSessionSettings
from backend.proxy.transport.domain_blocking import apply_domain_blocking


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _release_remote_session(
    *,
    api_url: str,
    api_key: str,
    provider_session_id: str,
) -> datetime | None:
    headers = {"X-BB-API-Key": api_key}
    last_error: Exception | None = None
    async with httpx.AsyncClient(trust_env=False) as client:
        for delay in (0.0, 0.1, 0.25):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await client.patch(
                    f"{api_url}/sessions/{provider_session_id}",
                    headers=headers,
                    json={"status": "REQUEST_RELEASE"},
                )
                response.raise_for_status()
                last_error = None
                break
            except Exception as error:
                last_error = error
        session_state: dict[str, object] | None = None
        with suppress(Exception):
            response = await client.get(
                f"{api_url}/sessions/{provider_session_id}",
                headers=headers,
            )
            response.raise_for_status()
            value = response.json()
            if isinstance(value, dict):
                session_state = value
        if last_error is not None and (
            session_state is None
            or session_state.get("status")
            not in {"COMPLETED", "ERROR", "TIMED_OUT"}
        ):
            raise last_error
        if session_state is not None:
            return _timestamp(session_state.get("endedAt"))
    return None


class BrowserbaseProviderSession(WebSocketProviderSession):
    def __init__(
        self,
        websocket,
        *,
        api_url: str,
        api_key: str,
        provider_session_id: str,
        provider_started_at: datetime | None,
        session_timeout_seconds: int,
    ) -> None:
        super().__init__(
            ProviderName.BROWSERBASE,
            websocket,
            provider_session_id=provider_session_id,
            provider_started_at=provider_started_at,
            session_timeout_seconds=session_timeout_seconds,
        )
        self._api_url = api_url
        self._api_key = api_key
        self._released = False

    async def close(self) -> None:
        if self._released:
            return
        websocket_error: Exception | None = None
        try:
            await super().close()
        except Exception as error:
            websocket_error = error
            if self.provider_ended_at is None:
                self.provider_ended_at = datetime.now(UTC)
        ended_at = await _release_remote_session(
            api_url=self._api_url,
            api_key=self._api_key,
            provider_session_id=self.provider_session_id,
        )
        self._released = True
        if ended_at is not None:
            self.provider_ended_at = ended_at
        if websocket_error is not None:
            raise websocket_error


@dataclass(frozen=True, slots=True)
class BrowserbaseAdapter:
    api_url: str
    api_key: str
    project_id: str | None
    timeout_seconds: int
    provider: ProviderName = ProviderName.BROWSERBASE

    async def acquire(
        self,
        session: HarborSession,
        settings: ResolvedSessionSettings,
    ):
        if not self.api_key:
            raise RuntimeError("Browserbase is not configured")
        payload: dict[str, object] = {
            "keepAlive": False,
            "timeout": self.timeout_seconds,
            "userMetadata": {"harborSessionId": session.session_id},
        }
        if self.project_id:
            payload["projectId"] = self.project_id
        headers = {"X-BB-API-Key": self.api_key}
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                f"{self.api_url}/sessions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            value = response.json()
        provider_session_id = value.get("id")
        connect_url = value.get("connectUrl")
        if not isinstance(provider_session_id, str):
            raise RuntimeError("Browserbase returned an invalid session")
        if not isinstance(connect_url, str):
            with suppress(Exception):
                await _release_remote_session(
                    api_url=self.api_url,
                    api_key=self.api_key,
                    provider_session_id=provider_session_id,
                )
            raise RuntimeError("Browserbase returned an invalid session")
        try:
            websocket = await connect(connect_url, max_size=None, proxy=None)
        except BaseException:
            with suppress(Exception):
                await _release_remote_session(
                    api_url=self.api_url,
                    api_key=self.api_key,
                    provider_session_id=provider_session_id,
                )
            raise
        return apply_domain_blocking(
            BrowserbaseProviderSession(
                websocket,
                api_url=self.api_url,
                api_key=self.api_key,
                provider_session_id=provider_session_id,
                provider_started_at=_timestamp(value.get("startedAt")),
                session_timeout_seconds=self.timeout_seconds,
            ),
            settings.blocked_domain_patterns,
        )
