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
    SessionDomain,
    SessionDomainCommand,
)

__all__ = [
    "AcquisitionAttempt",
    "Domain",
    "DomainCommandStat",
    "DomainPromotionStat",
    "FleetConfigurationEvent",
    "GatewaySession",
    "GatewayState",
    "ProviderState",
    "ProviderFleet",
    "ProviderInstance",
    "SessionDomain",
    "SessionDomainCommand",
    "SessionEventRecord",
]
