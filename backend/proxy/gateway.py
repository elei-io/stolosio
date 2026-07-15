import asyncio
import logging
from contextlib import suppress

from fastapi import WebSocket

from backend.proxy.adapters import get_provider_adapter
from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import RequestedSessionSettings, ResolvedSessionSettings
from backend.proxy.errors import (
    ConnectionRejected,
    ProviderAcquisitionTimeout,
    ProviderConnectionLost,
    ProviderUnavailable,
    SessionLeaseLost,
)
from backend.proxy.sessions import SessionManager
from backend.proxy.transport import relay_cdp

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(
        self,
        sessions: SessionManager,
        capabilities: CapabilityRegistry,
        acquisition_timeout_seconds: float,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities
        self._acquisition_timeout_seconds = acquisition_timeout_seconds

    async def connect(
        self,
        websocket: WebSocket,
        requested: RequestedSessionSettings,
        resolved: ResolvedSessionSettings,
    ) -> None:
        try:
            lease = await self._sessions.admit(requested, resolved)
        except ConnectionRejected:
            raise
        except Exception as error:
            raise ProviderUnavailable from error
        provider_session = None
        accepted = False
        failed = False
        reason = "client_disconnected"
        try:
            adapter = get_provider_adapter(resolved.provider.slug)
            try:
                async with asyncio.timeout(self._acquisition_timeout_seconds):
                    provider_session = await adapter.acquire(lease.session, resolved)
            except TimeoutError as error:
                raise ProviderAcquisitionTimeout from error
            except NotImplementedError as error:
                raise ProviderUnavailable from error
            except Exception as error:
                raise ProviderUnavailable from error

            await lease.connected()
            await websocket.accept()
            accepted = True

            relay_task = asyncio.create_task(
                relay_cdp(
                    websocket,
                    provider_session,
                    resolved.provider.slug,
                    self._capabilities,
                )
            )
            lost_task = asyncio.create_task(lease.wait_lost())
            done, pending = await asyncio.wait(
                {relay_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if lost_task in done:
                raise SessionLeaseLost
            await relay_task
        except ConnectionRejected as error:
            failed = True
            reason = error.reason
            if accepted:
                with suppress(RuntimeError):
                    await websocket.close(code=error.close_code, reason=error.reason)
            raise
        except Exception as error:
            failed = True
            reason = "provider_connection_lost"
            if accepted:
                with suppress(RuntimeError):
                    await websocket.close(code=1011, reason=reason)
            raise ProviderConnectionLost from error
        finally:
            if provider_session is not None:
                with suppress(Exception):
                    await provider_session.close()
            try:
                await lease.release(failed=failed, reason=reason)
            except Exception:
                logger.exception("Failed to release Harbor session %s", lease.session.session_id)
