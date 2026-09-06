"""Unified browser contracts."""

from backend.proxy.contracts.provider import (
    ACTIVE_PROVIDERS,
    PROMOTION_PROVIDERS,
    ProviderAdapter,
    ProviderName,
    ProviderSession,
)
from backend.proxy.contracts.session import (
    AttemptState,
    ProviderAttempt,
    SessionState,
    StolosioSession,
)
from backend.proxy.contracts.settings import (
    ProviderSelection,
    ProviderSettingSchema,
    RequestedSessionSettings,
    ResolvedSessionSettings,
    SessionSettingSchema,
    SettingSource,
    SettingsResolutionContext,
)

__all__ = [
    "ACTIVE_PROVIDERS",
    "PROMOTION_PROVIDERS",
    "ProviderAdapter",
    "ProviderAttempt",
    "ProviderName",
    "ProviderSession",
    "ProviderSelection",
    "ProviderSettingSchema",
    "RequestedSessionSettings",
    "ResolvedSessionSettings",
    "SessionSettingSchema",
    "SettingSource",
    "SettingsResolutionContext",
    "StolosioSession",
    "AttemptState",
    "SessionState",
]
