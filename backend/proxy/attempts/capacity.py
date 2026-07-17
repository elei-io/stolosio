from dataclasses import dataclass

from backend.fleet.providers import managed_fleet_definition
from backend.proxy.contracts import ProviderName
from backend.settings import Settings


@dataclass(frozen=True, slots=True)
class ProviderCapacity:
    max_active: int
    max_queued: int


def provider_capacity(settings: Settings, provider: ProviderName) -> ProviderCapacity:
    prefix = provider.value
    if managed_fleet_definition(provider) is not None:
        max_active = (
            getattr(settings, f"{prefix}_maximum_instances")
            * getattr(settings, f"{prefix}_session_capacity_per_instance")
        )
    else:
        max_active = getattr(settings, f"{prefix}_max_active_sessions")
    return ProviderCapacity(
        max_active=max_active,
        max_queued=getattr(settings, f"{prefix}_max_queued_attempts"),
    )
