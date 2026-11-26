"""Use OneDragon's server day and task list to limit daily inventory runs."""
from one_dragon.base.operation.application_run_record import AppRunRecord
from one_dragon.utils.os_utils import get_sunday_dt

from .drive_disc_manager_const import APP_ID
from .store import config


class ManagerRunRecord(AppRunRecord):
    def __init__(self, ctx, account):
        self.root = ctx.get_plugin_data_dir(APP_ID)/str(account)
        super().__init__(APP_ID, account, ctx.game_account_config.game_refresh_hour_offset)

    def counts(self, day):
        saved = self.get('manager_counts', None)
        if saved is None:
            # Preserve today's successful run when upgrading from the native daily counter.
            saved = {self.dt:1} if self.run_status == self.STATUS_SUCCESS else {}
        week = get_sunday_dt(day)
        return {d:n for d,n in saved.items() if get_sunday_dt(d) == week}

    @property
    def is_done(self):
        day = self.get_current_dt()
        counts = self.counts(day)
        cfg = config(self.root)
        return counts.get(day, 0) >= cfg['dailyLimit'] or sum(counts.values()) >= cfg['weeklyLimit']

    @property
    def run_status_under_now(self):
        if self.is_done:
            return self.STATUS_SUCCESS
        if self.dt == self.get_current_dt() and self.run_status in (self.STATUS_RUNNING, self.STATUS_FAIL):
            return self.run_status
        return self.STATUS_WAIT

    def update_status(self, new_status, only_status=False):
        if not only_status:
            day = self.get_current_dt()
            counts = self.counts(day)
            if new_status == self.STATUS_SUCCESS and self.run_status == self.STATUS_RUNNING:
                counts[day] = counts.get(day, 0) + 1
            self.update('manager_counts', counts, False)
        super().update_status(new_status, only_status)

    def check_and_update_status(self):
        # GroupApplication reads is_done, including when a player changes today's limit.
        if self.dt != self.get_current_dt() and self.run_status != self.STATUS_WAIT:
            self.update('manager_counts', self.counts(self.get_current_dt()), False)
            self.reset_record()
