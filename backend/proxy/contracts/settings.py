from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from backend.proxy.contracts.provider import ProviderName


class ProviderSelection(StrEnum):
    AUTO = "auto"
    HTTP = ProviderName.HTTP
    BROWSERLESS = ProviderName.BROWSERLESS
    BROWSERBASE = ProviderName.BROWSERBASE


class SettingSource(StrEnum):
    EXPLICIT = "explicit"
    AUTO = "auto"
    DEFAULT = "default"


class ProviderSettingSchema(BaseModel):
    slug: ProviderName | None = None


class SessionSettingSchema(BaseModel):
    reference: UUID | None = None


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

    @property
    def session_reference(self) -> UUID | None:
        value = self.overrides.get("harbor.session.reference")
        return value if isinstance(value, UUID) else None


@dataclass(frozen=True, slots=True)
class ResolvedSessionSettings:
    provider: ProviderSettingSchema
    session: SessionSettingSchema
    sources: dict[str, SettingSource]


@dataclass(frozen=True, slots=True)
class SettingsResolutionContext:
    target_url: str | None = None
