"""Hot-path escalation from the bounded HTTP facade to a browser provider.

The underlying transition package retains historical storage and event names. New
runtime code uses escalation terminology; background probes belong to promotion.
"""

from backend.proxy.provider_transition import (
    ProviderTransitionError,
    ProviderTransitionRepository,
    ProviderTransitionSession,
)

EscalationError = ProviderTransitionError
EscalationHistoryRepository = ProviderTransitionRepository
EscalatingProviderSession = ProviderTransitionSession

__all__ = [
    "EscalatingProviderSession",
    "EscalationError",
    "EscalationHistoryRepository",
]
