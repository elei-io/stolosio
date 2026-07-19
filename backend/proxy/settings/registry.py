from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from backend.proxy.contracts import RequestedSessionSettings
from backend.proxy.errors import InvalidHarborSettings
from backend.proxy.settings.base import BaseHarborSetting
from backend.proxy.settings.provider import HarborProviderSetting
from backend.proxy.settings.session import HarborSessionSetting


@dataclass(frozen=True, slots=True)
class RegisteredField:
    setting_slug: str
    model_field: str
    query: str
    adapter: TypeAdapter[Any]
    automatic: bool


class HarborSettingsRegistry:
    def __init__(self, settings: tuple[type[BaseHarborSetting[Any]], ...]) -> None:
        self._settings = settings
        self._fields: dict[str, RegisteredField] = {}
        self._settings_by_slug: dict[str, type[BaseHarborSetting[Any]]] = {}

        for setting in settings:
            if setting.slug in self._settings_by_slug:
                raise ValueError(f"Duplicate Harbor setting slug: {setting.slug}")
            self._settings_by_slug[setting.slug] = setting
            for field_name, model_field in setting.schema.model_fields.items():
                query = f"{setting.query_prefix}.{field_name}"
                if query in self._fields:
                    raise ValueError(f"Duplicate Harbor query key: {query}")
                self._fields[query] = RegisteredField(
                    setting_slug=setting.slug,
                    model_field=field_name,
                    query=query,
                    adapter=TypeAdapter(model_field.annotation),
                    automatic=setting.automatic,
                )

    @property
    def queries(self) -> frozenset[str]:
        return frozenset(self._fields)

    @property
    def fields(self) -> tuple[RegisteredField, ...]:
        return tuple(self._fields.values())

    def parse(self, query_items: list[tuple[str, str]]) -> RequestedSessionSettings:
        overrides: dict[str, Any] = {}
        auto_fields: set[str] = set()
        seen: set[str] = set()

        for query, raw_value in query_items:
            if not query.startswith("harbor."):
                continue
            field = self._fields.get(query)
            if field is None or query in seen or not raw_value:
                raise InvalidHarborSettings
            seen.add(query)
            if raw_value == "auto":
                if not field.automatic:
                    raise InvalidHarborSettings
                auto_fields.add(query)
                continue
            try:
                value = field.adapter.validate_python(raw_value)
            except ValidationError as error:
                raise InvalidHarborSettings from error
            overrides[query] = value

        return RequestedSessionSettings(
            overrides=overrides,
            auto_fields=frozenset(auto_fields),
        )

    def defaults(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for setting in self._settings:
            model = setting.defaults()
            for field_name in setting.schema.model_fields:
                values[f"{setting.query_prefix}.{field_name}"] = getattr(model, field_name)
        return values

    def build_models(self, values: dict[str, Any]) -> dict[str, Any]:
        model_values: dict[str, dict[str, Any]] = {setting.slug: {} for setting in self._settings}
        for query, value in values.items():
            field = self._fields[query]
            model_values[field.setting_slug][field.model_field] = value
        return {
            setting.slug: setting.schema.model_validate(model_values[setting.slug])
            for setting in self._settings
        }


harbor_settings_registry = HarborSettingsRegistry((HarborProviderSetting, HarborSessionSetting))
