from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from backend.proxy.contracts.provider import ProviderName


class ProviderSelection(StrEnum):
    AUTO = "auto"
    CHROMIUM = ProviderName.CHROMIUM
    BROWSERLESS = ProviderName.BROWSERLESS
    LIGHTPANDA = ProviderName.LIGHTPANDA
    CAMOUFOX = ProviderName.CAMOUFOX


class SettingSource(StrEnum):
    EXPLICIT = "explicit"
    AUTO = "auto"
    DEFAULT = "default"


class ProviderSettingSchema(BaseModel):
    slug: ProviderName = ProviderName.CHROMIUM


@dataclass(frozen=True, slots=True)
class RequestedSessionSettings:
    overrides: dict[str, Any] = field(default_factory=dict)
    auto_fields: frozenset[str] = frozenset()

    @property
    def provider(self) -> ProviderSelection:
        value = self.overrides.get("harbor.provider.slug")
        if value is None:
            return ProviderSelection.AUTO
        return ProviderSelection(value)


@dataclass(frozen=True, slots=True)
class ResolvedSessionSettings:
    provider: ProviderSettingSchema
    sources: dict[str, SettingSource]


@dataclass(frozen=True, slots=True)
class SettingsResolutionContext:
    target_url: str | None = None
