from dataclasses import dataclass

from backend.fleet.contracts import RuntimeInstance
from backend.proxy.contracts import ProviderName


@dataclass(frozen=True, slots=True)
class ProviderFleetDefinition:
    """Provider facts needed to expose one runtime instance to Stolosio."""

    provider: ProviderName
    connection_port: int
    connection_path: str = ""
    default_session_capacity: int = 1

    def endpoint(self, instance: RuntimeInstance) -> str:
        return f"ws://{instance.address}:{self.connection_port}{self.connection_path}"
