from pathlib import Path
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.utils import os_utils
from zzz_od.application.zzz_application import ZApplication
from .drive_disc_manager_const import APP_ID, APP_NAME
from .service import preview, rule_snapshot
from .store import import_inventory, read
from .manual_equip import MANUAL_GROUP, checked_scan, manual_request, verify_selection


def wiki_path():
    home = Path(os_utils.get_work_dir())
    current = home/'plugin_data/wiki_data/zzz_translation.json'
    return current if current.is_file() else home/'plugins/inventory_scan/assets/wiki_data/zzz_translation.json'


class ManagerApp(ZApplication):
    def __init__(self,ctx,instance_idx,group_id):
        self.account = instance_idx
        self.group = group_id
        self.root = ctx.get_plugin_data_dir(APP_ID)/str(instance_idx)
        super().__init__(ctx,APP_ID,op_name=APP_NAME,need_check_game_win=False)
        if group_id == MANUAL_GROUP:
            self.run_record = None

    @operation_node(name='驱动盘管理', is_start_node=True, screenshot_before_round=False)
    def manage(self):
        from .maintenance import Deferred, exclusive
        try:
            with exclusive(self.root.parent/'_maintenance'):
                return self.manage_inventory()
        except Deferred as exc:
            return self.round_fail(str(exc))
        except Exception as exc:
            if self.group == MANUAL_GROUP:
                return self.round_fail(str(exc))
            raise

    def manage_inventory(self):
        try:
            if self.ctx.current_instance_idx != self.account:
                return self.round_fail('当前账号已改变')
            request = None
            if self.group == MANUAL_GROUP:
                request, guard = manual_request(self.account)
                interruption = None
                def check_session():
                    nonlocal interruption
                    if interruption:
                        raise ValueError(interruption)
                    try:
                        if self.ctx.current_instance_idx != self.account or not self.ctx.run_context.is_context_running:
                            raise ValueError('换装已停止或当前账号已变化')
                        guard()
                    except Exception as exc:
                        interruption = str(exc) or '游戏状态已变化'
                        raise
                self.guard_game = check_session
                self.guard_game()
            pinned = rule_snapshot(self.root, refresh=request is None)
            cfg = pinned[0]
            if request:
                from .store import digest
                if digest(cfg) != request['configuration']:
                    return self.round_fail('配队配置已变化，请重新预览')
            field = request['name'] if request else cfg.get('fieldProfile')
            if field:
                from .loadout import verify_bindings
                verify_bindings(cfg, field, read(wiki_path(), {}))
            mark = request is None and cfg.get('applyMarkers')
            scanner = None
            if request or cfg['run_scanner']:
                from time import time
                from .scanner_status import scanner_status
                ready, reason = scanner_status(self.ctx, self.account)
                if not ready:
                    return self.round_fail(reason)
                scan_started = time()
                exports = Path(os_utils.get_work_dir())/'.debug/inventory_exports'
                before = {p:p.stat().st_mtime_ns for p in exports.glob('inventory_*.json')}
                scanner = self.ctx.run_context.get_application('inventory_scan',self.account,'one_dragon' if request else self.group)
                if not scanner.config.scan_drive_disk:
                    return self.round_fail('请在仓库扫描器中启用驱动盘')
                # Suppress the scanner's optional browser launch for this invocation only.
                from unittest.mock import patch
                from .scan_marks import capture_marks
                with capture_marks(scanner), patch.object(scanner,'_open_export',lambda path: None):
                    result = checked_scan(scanner, self.guard_game) if request else scanner.execute()
                if not result.success or self.ctx.current_instance_idx != self.account:
                    return self.round_fail('本轮扫描中断，请核对账号及库存')
                fresh = [p for p in exports.glob('inventory_*.json') if before.get(p)!=p.stat().st_mtime_ns]
                if len(fresh)!=1:
                    return self.round_fail('本轮扫描文件无法唯一识别')
                import_inventory(self.root,fresh[0],readings=scanner.ocr_worker.scanned_discs)
                from .marks import reconcile_scan
                from .salvage_audit import native_run, observe_native, reconcile
                snapshot = read(self.root/'inventory.json')
                observe_native(self.root, self.account, native_run(Path(os_utils.get_work_dir()), self.account), scan_started)
                reconcile(self.root,snapshot)
                from .store import equipment_names
                names = equipment_names(read(wiki_path(), {}))
                names.update(cfg.get('aliases', {}))
                reconcile_scan(self.root, snapshot, names)
            report = preview(self.root,wiki_path(),snapshot=pinned)
            if request:
                self.guard_game()
                verify_selection(request, cfg, report, snapshot)
            elif report['issues']:
                return self.round_fail('配装待复核 · '+' / '.join(report['issues']))
            if field and scanner is None:
                return self.round_fail('应用出场方案需要本轮重新扫描')
            messages = []
            if mark:
                if scanner is None:
                    return self.round_fail('请开启本轮重新扫描后再标记')
                from .live import Marker
                observed = Marker(self,scanner,report).run()
            if field:
                from .loadout import apply_loadout
                from .equip_ui import EquipUI
                receipt = apply_loadout(self.root,cfg,report,snapshot,EquipUI(self,scanner,wiki_path()),name=field)
                messages.append(f"{receipt['profile']} · 已复读 {len(receipt['verified'])} 个号位")
            if mark:
                from .dismantle import handoff
                messages.append(handoff(self,report,observed))
            return self.round_success(' / '.join(messages) if messages else '预览 · '+str(report['counts']))
        except (KeyError,ValueError,OSError,TypeError) as exc:
            return self.round_fail(str(exc))
