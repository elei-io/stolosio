from backend.proxy.contracts import SessionSettingSchema
from backend.proxy.settings.base import BaseHarborSetting


class HarborSessionSetting(BaseHarborSetting[SessionSettingSchema]):
    slug = "session"
    query_prefix = "harbor.session"
    schema = SessionSettingSchema
    automatic = False
