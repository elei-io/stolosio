"""SQLAlchemy persistence models."""

from backend.db.models.gateway import (
    AcquisitionAttempt,
    FleetConfigurationEvent,
    GatewaySession,
    GatewayState,
    ProviderFleet,
    ProviderInstance,
    ProviderState,
    SessionEventRecord,
)
from backend.db.models.observability import (
    Domain,
    DomainCommandStat,
    DomainProviderCostStat,
    DomainProviderHealth,
    DomainProviderRuntimeState,
    DomainProviderTransitionStat,
    HealthProbe,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
    SessionDomainCommand,
    SessionDomainProviderCompatibility,
)

__all__ = [
    "AcquisitionAttempt",
    "Domain",
    "DomainCommandStat",
    "DomainProviderCostStat",
    "DomainProviderHealth",
    "DomainProviderRuntimeState",
    "DomainProviderTransitionStat",
    "FleetConfigurationEvent",
    "GatewaySession",
    "GatewayState",
    "ProviderState",
    "ProviderFleet",
    "ProviderInstance",
    "ProviderRoutingProfile",
    "HealthProbe",
    "RoutingConfiguration",
    "SessionDomain",
    "SessionDomainCommand",
    "SessionDomainProviderCompatibility",
    "SessionEventRecord",
]
