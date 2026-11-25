from one_dragon.base.operation.application.application_config import ApplicationConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from . import drive_disc_manager_const as meta


class ManagerFactory(ApplicationFactory):
    def __init__(self, ctx):
        super().__init__(meta)
        self.ctx = ctx

    def create_application(self, instance_idx, group_id):
        from .manager_app import ManagerApp
        return ManagerApp(self.ctx, instance_idx, group_id)

    def create_config(self, instance_idx, group_id):
        return ApplicationConfig(meta.APP_ID,instance_idx,group_id)
