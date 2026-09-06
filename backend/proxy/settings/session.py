from backend.proxy.contracts import SessionSettingSchema
from backend.proxy.settings.base import BaseStolosioSetting


class StolosioSessionSetting(BaseStolosioSetting[SessionSettingSchema]):
    slug = "session"
    query_prefix = "stolosio.session"
    schema = SessionSettingSchema
    automatic = False
