from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

BROWSERLESS_FLEET = ProviderFleetDefinition(
    provider=ProviderName.BROWSERLESS,
    connection_port=3000,
    # The Compose service configures Browserless with CONCURRENT=5. Harbor may
    # advertise fewer slots, but must not admit more work than the process accepts.
    maximum_session_capacity=5,
)
