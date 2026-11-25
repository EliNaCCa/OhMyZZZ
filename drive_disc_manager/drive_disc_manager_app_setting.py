from one_dragon_qt.services.app_setting.app_setting_provider import AppSettingProvider, SettingType
from .drive_disc_manager_const import APP_ID


class DriveDiscSettings(AppSettingProvider):
    app_id = APP_ID
    setting_type = SettingType.INTERFACE

    @staticmethod
    def get_setting_cls():
        from .settings import ManagerInterface
        return ManagerInterface
