from backend.proxy.contracts import ProviderSettingSchema
from backend.proxy.settings.base import BaseStolosioSetting


class StolosioProviderSetting(BaseStolosioSetting[ProviderSettingSchema]):
    slug = "provider"
    query_prefix = "stolosio.provider"
    schema = ProviderSettingSchema
