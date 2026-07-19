import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from uuid import UUID

from fastapi import WebSocket
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.events.cdp import CdpEventObserver
from backend.events.publisher import EventPublisher, NullEventPublisher
from backend.proxy.adapters import get_provider_adapter
from backend.proxy.attempts import AttemptAdmission, AttemptLease
from backend.proxy.contracts import ProviderSelection, ResolvedSessionSettings
from backend.proxy.errors import (
    ConnectionRejected,
    ProviderAcquisitionTimeout,
    ProviderConnectionLost,
    ProviderUnavailable,
    SessionLeaseLost,
)
from backend.proxy.escalation import (
    EscalatingProviderSession,
    EscalationHistoryRepository,
)
from backend.proxy.network_policy import NetworkPolicyRepository
from backend.proxy.routing import RoutingRepository
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
        settings: Settings,
        event_publisher: EventPublisher | None = None,
        resolver: HarborSettingsResolver = harbor_settings_resolver,
        transition_repository: EscalationHistoryRepository | None = None,
        routing: RoutingRepository | None = None,
        network_policy: NetworkPolicyRepository | None = None,
    ) -> None:
        self._sessions = sessions
        self._attempts = attempts
        self._settings = settings
        self._event_publisher = event_publisher or NullEventPublisher()
        self._resolver = resolver
        self._transition_repository = transition_repository
        self._routing = routing
        self._network_policy = network_policy

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
            if self._network_policy is not None:
                network_policy = await self._network_policy.settings()
                resolved = replace(
                    resolved,
                    blocked_domain_patterns=network_policy.blocked_domain_patterns,
                    network_policy_version=network_policy.configuration_version,
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

                automatic = requested.provider is ProviderSelection.AUTO
                if automatic:
                    if disconnected.done():
                        client_disconnected = True
                        reason = "client_disconnected"
                        return
                    if self._transition_repository is None:
                        raise RuntimeError("Provider transition repository is unavailable")
                    observer = CdpEventObserver(
                        UUID(session.session.session_id),
                        None,
                        None,
                        self._event_publisher,
                    )
                    provider_session = EscalatingProviderSession(
                        session.session,
                        resolved,
                        self._attempts,
                        self._transition_repository,
                        observer,
                        self._settings,
                        self._routing,
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

            await session.open()
            if attempt is not None:
                await attempt.activate()
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
                await self._bounded_cleanup(provider_session.close(), "provider session")
            if attempt is not None:
                command_summary = (
                    observer.command_summary(UUID(attempt.attempt.attempt_id))
                    if observer is not None
                    else None
                )
                await self._bounded_cleanup(
                    attempt.record_provider_usage(
                        provider_ended_at=getattr(
                            provider_session,
                            "provider_ended_at",
                            None,
                        )
                    ),
                    "provider usage",
                )
                await self._bounded_cleanup(
                    attempt.release(
                        failed=failed,
                        reason=reason,
                        command_summary=command_summary,
                    ),
                    "attempt",
                )
                if observer is not None:
                    await self._bounded_cleanup(
                        observer.flush_command_summary(UUID(attempt.attempt.attempt_id)),
                        "command summary",
                    )
            if session is not None:
                await self._bounded_cleanup(
                    session.release(
                        failed=failed,
                        reason=reason if failed or client_disconnected else "client_disconnected",
                    ),
                    "session",
                )
            if observer is not None:
                await self._bounded_cleanup(
                    observer.flush_command_summaries(),
                    "command summaries",
                )

    async def _prepare(
        self,
        session: SessionLease,
        resolved: ResolvedSessionSettings,
    ):
        attempt: AttemptLease | None = None
        provider_session = None
        try:
            if resolved.provider.slug is None:
                raise RuntimeError("Direct provider preparation requires a concrete provider")
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
                        await attempt.bind_provider_session(
                            provider_session_id=getattr(
                                provider_session,
                                "provider_session_id",
                                None,
                            ),
                            provider_started_at=getattr(
                                provider_session,
                                "provider_started_at",
                                None,
                            ),
                        )
                except TimeoutError as error:
                    raise ProviderAcquisitionTimeout from error
                except NotImplementedError as error:
                    raise ProviderUnavailable from error
                except Exception as error:
                    raise ProviderUnavailable from error
                return attempt, provider_session
        except TimeoutError as error:
            await self._cleanup_preparation(
                attempt,
                provider_session,
                reason="provider_acquisition_timeout",
            )
            raise ProviderAcquisitionTimeout from error
        except BaseException as error:
            reason = (
                error.reason
                if isinstance(error, ConnectionRejected)
                else "client_disconnected"
                if isinstance(error, asyncio.CancelledError)
                else "provider_unavailable"
            )
            await self._cleanup_preparation(
                attempt,
                provider_session,
                reason=reason,
            )
            raise

    async def _cleanup_preparation(
        self,
        attempt: AttemptLease | None,
        provider_session,
        *,
        reason: str,
    ) -> None:
        if provider_session is not None:
            await self._bounded_cleanup(provider_session.close(), "provider session")
        if attempt is None:
            return
        await self._bounded_cleanup(
            attempt.record_provider_usage(
                provider_ended_at=getattr(
                    provider_session,
                    "provider_ended_at",
                    None,
                )
            ),
            "provider usage",
        )
        await self._bounded_cleanup(
            attempt.release(failed=True, reason=reason),
            "attempt",
        )

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
                response = Response(status_code=error.status_code)
                # Uvicorn supplies Content-Length for WebSocket denial responses.
                # Starlette also adds it to Response, producing an invalid duplicate
                # header that strict CDP clients reject before seeing the status.
                response.raw_headers = [
                    (name, value)
                    for name, value in response.raw_headers
                    if name.lower() != b"content-length"
                ]
                await websocket.send_denial_response(response)
            elif websocket.application_state is WebSocketState.CONNECTED:
                await websocket.close(code=error.close_code, reason=error.reason)
        except (OSError, RuntimeError, WebSocketDisconnect):
            return
