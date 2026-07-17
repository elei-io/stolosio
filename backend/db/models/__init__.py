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
    DomainPromotionStat,
    DomainProviderProfile,
    ProviderRoutingProfile,
    QualificationProbe,
    RoutingConfiguration,
    SessionDomain,
    SessionDomainCommand,
)

__all__ = [
    "AcquisitionAttempt",
    "Domain",
    "DomainCommandStat",
    "DomainProviderProfile",
    "DomainPromotionStat",
    "FleetConfigurationEvent",
    "GatewaySession",
    "GatewayState",
    "ProviderState",
    "ProviderFleet",
    "ProviderInstance",
    "ProviderRoutingProfile",
    "QualificationProbe",
    "RoutingConfiguration",
    "SessionDomain",
    "SessionDomainCommand",
    "SessionEventRecord",
]
