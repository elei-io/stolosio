from backend.fleet.providers.contracts import ProviderFleetDefinition
from backend.proxy.contracts import ProviderName

# The Camoufox mapping server owns one browser connection, so each managed
# instance contributes one Harbor session slot.
CAMOUFOX_FLEET = ProviderFleetDefinition(
    provider=ProviderName.CAMOUFOX,
    connection_port=1234,
    connection_path="/harbor",
    maximum_session_capacity=1,
)
