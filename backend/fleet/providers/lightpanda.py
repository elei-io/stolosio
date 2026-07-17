from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

# Lightpanda currently supports one browser context and one page target, so every
# managed instance contributes exactly one slot. The slot count itself is enforced by
# the durable fleet configuration rather than by this transport definition.
LIGHTPANDA_FLEET = ProviderFleetDefinition(
    provider=ProviderName.LIGHTPANDA,
    connection_port=9222,
    maximum_session_capacity=1,
)
