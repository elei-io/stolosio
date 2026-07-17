import asyncio
import logging
from contextlib import suppress
from uuid import UUID

from fastapi import WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.events.cdp import CdpEventObserver
from backend.events.publisher import EventPublisher, NullEventPublisher
from backend.proxy.adapters import get_provider_adapter
from backend.proxy.attempts import AttemptAdmission, AttemptLease
from backend.proxy.capabilities import CapabilityRegistry
from backend.proxy.contracts import ProviderName, ProviderSelection, ResolvedSessionSettings
from backend.proxy.errors import (
    ConnectionRejected,
    ProviderAcquisitionTimeout,
    ProviderConnectionLost,
    ProviderUnavailable,
    SessionLeaseLost,
)
from backend.proxy.no_browser import AdaptiveCdpSession, PromotionHistoryRepository
from backend.proxy.sessions import SessionAdmission, SessionLease
from backend.proxy.settings import HarborSettingsResolver, harbor_settings_resolver
from backend.proxy.transport import relay_cdp
from backend.settings import Settings

logger = logging.getLogger(__name__)


class Gateway:
    def __init__(
        self,
        sessions: SessionAdmission,
        attempts: AttemptAdmission,
        capabilities: CapabilityRegistry,
        settings: Settings,
        event_publisher: EventPublisher | None = None,
        resolver: HarborSettingsResolver = harbor_settings_resolver,
        promotion_history: PromotionHistoryRepository | None = None,
    ) -> None:
        self._sessions = sessions
        self._attempts = attempts
        self._capabilities = capabilities
        self._settings = settings
        self._event_publisher = event_publisher or NullEventPublisher()
        self._resolver = resolver
        self._promotion_history = promotion_history

    async def connect(self, websocket: WebSocket) -> None:
        session: SessionLease | None = None
        attempt: AttemptLease | None = None
        provider_session = None
        observer = None
        accepted = False
        client_disconnected = False
        failed = False
        reason = "client_disconnected"
        try:
            requested, resolved = await self._resolver.resolve(
                list(websocket.query_params.multi_items())
            )
            initial = await websocket.receive()
            if initial["type"] == "websocket.disconnect":
                return
            disconnected = asyncio.create_task(self._wait_for_disconnect(websocket))
            try:
                admission = asyncio.create_task(self._sessions.admit(requested))
                done, _ = await asyncio.wait(
                    {admission, disconnected},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnected in done:
                    client_disconnected = True
                    reason = "client_disconnected"
                    if admission.done() and not admission.cancelled():
                        result = admission.exception()
                        if result is None:
                            session = admission.result()
                    else:
                        admission.cancel()
                        await asyncio.gather(admission, return_exceptions=True)
                    return
                session = await admission

                adaptive = requested.provider in {
                    ProviderSelection.AUTO,
                    ProviderSelection.HTTP,
                }
                if adaptive:
                    if disconnected.done():
                        client_disconnected = True
                        reason = "client_disconnected"
                        return
                    if self._promotion_history is None:
                        raise RuntimeError("No-browser promotion history is unavailable")
                    observer = CdpEventObserver(
                        UUID(session.session.session_id),
                        None,
                        ProviderName.HTTP,
                        self._event_publisher,
                    )
                    provider_session = AdaptiveCdpSession(
                        session.session,
                        resolved,
                        self._attempts,
                        self._capabilities,
                        self._promotion_history,
                        observer,
                        self._settings,
                        force_http=requested.provider is ProviderSelection.HTTP,
                    )
                else:
                    preparation = asyncio.create_task(self._prepare(session, resolved))
                    lease_lost = asyncio.create_task(session.wait_lost())
                    try:
                        done, _ = await asyncio.wait(
                            {preparation, disconnected, lease_lost},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if disconnected in done:
                            client_disconnected = True
                            reason = "client_disconnected"
                            if preparation.done() and not preparation.cancelled():
                                result = preparation.exception()
                                if result is None:
                                    attempt, provider_session = preparation.result()
                            else:
                                preparation.cancel()
                                await asyncio.gather(preparation, return_exceptions=True)
                            return
                        if lease_lost in done:
                            preparation.cancel()
                            await asyncio.gather(preparation, return_exceptions=True)
                            raise SessionLeaseLost
                        attempt, provider_session = await preparation
                    finally:
                        if not lease_lost.done():
                            lease_lost.cancel()
                        await asyncio.gather(lease_lost, return_exceptions=True)
            finally:
                if not disconnected.done():
                    disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)

            if attempt is not None:
                await attempt.activate()
            await session.open()
            await websocket.accept()
            accepted = True

            if observer is None:
                assert attempt is not None
                observer = CdpEventObserver(
                    UUID(session.session.session_id),
                    UUID(attempt.attempt.attempt_id),
                    attempt.attempt.provider,
                    self._event_publisher,
                )

            relay_task = asyncio.create_task(
                relay_cdp(
                    websocket,
                    provider_session,
                    provider_session.provider,
                    self._capabilities,
                    observer,
                )
            )
            lease_lost = asyncio.create_task(session.wait_lost())
            done, pending = await asyncio.wait(
                {relay_task, lease_lost},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if lease_lost in done:
                raise SessionLeaseLost
            await relay_task
        except ConnectionRejected as error:
            failed = True
            reason = error.reason
            await self._reject(websocket, error)
        except WebSocketDisconnect:
            client_disconnected = True
            reason = "client_disconnected"
        except Exception:
            failed = True
            reason = "provider_connection_lost" if accepted else "provider_unavailable"
            logger.exception("Harbor connection failed")
            await self._reject(
                websocket,
                ProviderConnectionLost() if accepted else ProviderUnavailable(),
            )
        finally:
            if provider_session is not None:
                if failed and hasattr(provider_session, "fail"):
                    with suppress(Exception):
                        await provider_session.fail(reason)
                with suppress(Exception):
                    await provider_session.close()
            if attempt is not None:
                await self._bounded_cleanup(
                    attempt.release(failed=failed, reason=reason),
                    "attempt",
                )
            if session is not None:
                await self._bounded_cleanup(
                    session.release(
                        failed=failed,
                        reason=reason if failed or client_disconnected else "client_disconnected",
                    ),
                    "session",
                )

    async def _prepare(
        self,
        session: SessionLease,
        resolved: ResolvedSessionSettings,
    ):
        attempt: AttemptLease | None = None
        try:
            total_timeout = (
                self._settings.provider_queue_timeout_seconds
                + self._settings.provider_acquisition_timeout_seconds
            )
            async with asyncio.timeout(total_timeout):
                attempt = await self._attempts.acquire(session.session, resolved)
                adapter = get_provider_adapter(
                    resolved.provider.slug,
                    endpoint=attempt.attempt.endpoint,
                )
                try:
                    async with asyncio.timeout(self._settings.provider_acquisition_timeout_seconds):
                        provider_session = await adapter.acquire(session.session, resolved)
                except TimeoutError as error:
                    raise ProviderAcquisitionTimeout from error
                except NotImplementedError as error:
                    raise ProviderUnavailable from error
                except Exception as error:
                    raise ProviderUnavailable from error
                return attempt, provider_session
        except TimeoutError as error:
            if attempt is not None:
                await self._bounded_cleanup(
                    attempt.release(failed=True, reason="provider_acquisition_timeout"),
                    "attempt",
                )
            raise ProviderAcquisitionTimeout from error
        except BaseException as error:
            if attempt is not None:
                reason = (
                    error.reason
                    if isinstance(error, ConnectionRejected)
                    else "client_disconnected"
                    if isinstance(error, asyncio.CancelledError)
                    else "provider_unavailable"
                )
                await self._bounded_cleanup(
                    attempt.release(failed=True, reason=reason),
                    "attempt",
                )
            raise

    async def _bounded_cleanup(self, cleanup, resource: str) -> None:
        try:
            async with asyncio.timeout(self._settings.session_cleanup_timeout_seconds):
                await cleanup
        except Exception:
            logger.exception("Failed to release Harbor %s within cleanup budget", resource)

    @staticmethod
    async def _wait_for_disconnect(websocket: WebSocket) -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    @staticmethod
    async def _reject(websocket: WebSocket, error: ConnectionRejected) -> None:
        if (
            websocket.application_state is WebSocketState.DISCONNECTED
            or websocket.client_state is WebSocketState.DISCONNECTED
        ):
            return
        try:
            if websocket.application_state is WebSocketState.CONNECTING:
                await websocket.send_denial_response(Response(status_code=error.status_code))
            elif websocket.application_state is WebSocketState.CONNECTED:
                await websocket.close(code=error.close_code, reason=error.reason)
        except (OSError, RuntimeError, WebSocketDisconnect):
            return
