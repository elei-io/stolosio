from dataclasses import dataclass

from backend.proxy.contracts import ProviderName
from backend.settings import Settings


@dataclass(frozen=True, slots=True)
class ProviderCapacity:
    max_active: int
    max_queued: int


def provider_capacity(settings: Settings, provider: ProviderName) -> ProviderCapacity:
    prefix = provider.value
    return ProviderCapacity(
        max_active=getattr(settings, f"{prefix}_max_active_sessions"),
        max_queued=getattr(settings, f"{prefix}_max_queued_sessions"),
    )
