from typing import ClassVar

from pydantic import BaseModel


class BaseHarborSetting[SettingSchema: BaseModel]:
    slug: ClassVar[str]
    query_prefix: ClassVar[str]
    schema: ClassVar[type[SettingSchema]]
    automatic: ClassVar[bool] = True

    @classmethod
    def defaults(cls) -> SettingSchema:
        return cls.schema()
