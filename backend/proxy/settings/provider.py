from backend.proxy.contracts import ProviderSettingSchema
from backend.proxy.settings.base import BaseHarborSetting


class HarborProviderSetting(BaseHarborSetting[ProviderSettingSchema]):
    slug = "provider"
    query_prefix = "harbor.provider"
    schema = ProviderSettingSchema
