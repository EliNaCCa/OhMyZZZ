from one_dragon.base.operation.application.application_config import ApplicationConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from . import drive_disc_manager_const as meta


class ManagerFactory(ApplicationFactory):
    def __init__(self, ctx):
        super().__init__(meta)
        self.ctx = ctx
        # The host also discovers factories in headless runs. Only attach to Qt
        # when the GUI has already loaded it; discovery can be on its init thread.
        import sys
        if 'PySide6.QtWidgets' in sys.modules:
            from .update_prompt import attach
            attach(ctx)

    def create_application(self, instance_idx, group_id):
        from .manager_app import ManagerApp
        return ManagerApp(self.ctx, instance_idx, group_id)

    def create_config(self, instance_idx, group_id):
        return ApplicationConfig(meta.APP_ID,instance_idx,group_id)

    def create_run_record(self, instance_idx):
        from .frequency import ManagerRunRecord
        return ManagerRunRecord(self.ctx, instance_idx)
