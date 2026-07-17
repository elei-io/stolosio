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
    DomainProviderSupport,
    DomainProviderTransitionStat,
    ProviderRoutingProfile,
    RoutingConfiguration,
    SessionDomain,
    SessionDomainCommand,
    SupportProbe,
)

__all__ = [
    "AcquisitionAttempt",
    "Domain",
    "DomainCommandStat",
    "DomainProviderSupport",
    "DomainProviderTransitionStat",
    "FleetConfigurationEvent",
    "GatewaySession",
    "GatewayState",
    "ProviderState",
    "ProviderFleet",
    "ProviderInstance",
    "ProviderRoutingProfile",
    "SupportProbe",
    "RoutingConfiguration",
    "SessionDomain",
    "SessionDomainCommand",
    "SessionEventRecord",
]
