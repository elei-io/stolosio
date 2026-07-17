from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

CHROMIUM_FLEET = ProviderFleetDefinition(
    provider=ProviderName.CHROMIUM,
    connection_port=9222,
)
