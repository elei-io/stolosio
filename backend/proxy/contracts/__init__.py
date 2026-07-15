"""Unified browser contracts."""

from backend.proxy.contracts.provider import (
    ProviderAdapter,
    ProviderConnection,
    ProviderName,
)

__all__ = ["ProviderAdapter", "ProviderConnection", "ProviderName"]
