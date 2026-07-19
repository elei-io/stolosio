from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

BROWSERLESS_FLEET = ProviderFleetDefinition(
    provider=ProviderName.BROWSERLESS,
    connection_port=3000,
    default_session_capacity=5,
)
