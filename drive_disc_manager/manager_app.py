from pathlib import Path
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.utils import os_utils
from zzz_od.application.zzz_application import ZApplication
from .drive_disc_manager_const import APP_ID, APP_NAME
from .service import preview
from .store import config, import_inventory, read


def wiki_path():
    return Path(os_utils.get_work_dir())/'plugins/inventory_scan/assets/wiki_data/zzz_translation.json'


class ManagerApp(ZApplication):
    def __init__(self,ctx,instance_idx,group_id):
        self.account = instance_idx
        self.group = group_id
        self.root = ctx.get_plugin_data_dir(APP_ID)/str(instance_idx)
        super().__init__(ctx,APP_ID,op_name=APP_NAME,need_check_game_win=False)

    @operation_node(name='驱动盘管理', is_start_node=True, screenshot_before_round=False)
    def manage(self):
        try:
            if self.ctx.current_instance_idx != self.account:
                return self.round_fail('当前账号已改变')
            cfg = config(self.root)
            scanner = None
            if cfg['run_scanner']:
                exports = Path(os_utils.get_work_dir())/'.debug/inventory_exports'
                before = {p:p.stat().st_mtime_ns for p in exports.glob('inventory_*.json')}
                scanner = self.ctx.run_context.get_application('inventory_scan',self.account,self.group)
                if not scanner.config.scan_drive_disk:
                    return self.round_fail('请在仓库扫描器中启用驱动盘')
                # Suppress the scanner's optional browser launch for this invocation only.
                from unittest.mock import patch
                from .scan_marks import capture_marks
                with capture_marks(scanner), patch.object(scanner,'_open_export',lambda path: None):
                    result = scanner.execute()
                if not result.success or self.ctx.current_instance_idx != self.account:
                    return self.round_fail('本轮扫描中断，请核对账号及库存')
                fresh = [p for p in exports.glob('inventory_*.json') if before.get(p)!=p.stat().st_mtime_ns]
                if len(fresh)!=1:
                    return self.round_fail('本轮扫描文件无法唯一识别')
                import_inventory(self.root,fresh[0])
                from .marks import reconcile_scan
                from .salvage_audit import reconcile
                snapshot = read(self.root/'inventory.json')
                reconcile(self.root,snapshot)
                reconcile_scan(self.root,snapshot)
            report = preview(self.root,wiki_path())
            if report['issues']:
                return self.round_fail('配装待复核 · '+' / '.join(report['issues']))
            if cfg.get('applyMarkers'):
                if scanner is None:
                    return self.round_fail('请开启本轮重新扫描后再标记')
                from .live import Marker
                observed = Marker(self,scanner,report).run()
                from .dismantle import handoff
                return self.round_success(handoff(self,report,observed))
            return self.round_success('预览 · '+str(report['counts']))
        except (KeyError,ValueError,OSError,TypeError) as exc:
            return self.round_fail(str(exc))
