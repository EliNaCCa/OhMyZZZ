from pathlib import Path
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.utils import os_utils
from zzz_od.application.zzz_application import ZApplication
from .drive_disc_manager_const import APP_ID, APP_NAME
from .service import preview, rule_snapshot
from .store import import_inventory, read
from .manual_equip import MANUAL_GROUP, SCAN_GROUP, SelectionChanged, checked_scan, manual_request, verify_selection


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
        if group_id in (MANUAL_GROUP, SCAN_GROUP):
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
            if self.group in (MANUAL_GROUP, SCAN_GROUP):
                return self.round_fail(str(exc))
            raise

    def manage_inventory(self):
        try:
            if self.ctx.current_instance_idx != self.account:
                return self.round_fail('当前账号已改变')
            request = None
            scan_only = self.group == SCAN_GROUP
            interactive = self.group in (MANUAL_GROUP, SCAN_GROUP)
            if interactive:
                request, guard = manual_request(self.account, self.group)
                interruption = None
                def check_session():
                    nonlocal interruption
                    if interruption:
                        raise ValueError(interruption)
                    try:
                        if self.ctx.current_instance_idx != self.account or not self.ctx.run_context.is_context_running:
                            raise ValueError('操作已停止或当前账号已变化')
                        guard()
                    except Exception as exc:
                        interruption = str(exc) or '游戏状态已变化'
                        raise
                self.guard_game = check_session
                self.guard_game()
            pinned = rule_snapshot(self.root, refresh=not interactive)
            cfg = pinned[0]
            if self.group == MANUAL_GROUP:
                from .store import digest
                if digest(cfg) != request['configuration'] or request['rules'] != pinned[1].get('version'):
                    return self.round_fail('配队配置或规则已更新，请重新计算')
            field = None if scan_only else (request['name'] if request else cfg.get('fieldProfile'))
            if field:
                from .loadout import verify_bindings
                verify_bindings(cfg, field, read(wiki_path(), {}))
            mark = not interactive and cfg.get('applyMarkers')
            source = cfg['inventorySource']
            network = source in ('cn_export', 'network')
            cn_inventory = cn_plan = None
            manual_cn = self.group == MANUAL_GROUP and source == 'cn_export'
            if manual_cn or (network and cfg['run_scanner'] and not interactive):
                def same_account():
                    if self.ctx.current_instance_idx != self.account or not self.ctx.run_context.is_context_running:
                        raise ValueError('账号已切换或任务已停止')
                if source == 'cn_export':
                    from .cn_export import refresh
                    refresh(self.root, same_account, showcase_uid=cfg.get('showcaseUid', ''), wiki=read(wiki_path(), {}))
                else:
                    from .network_reader import capture
                    capture(self.root, same_account)
                if mark or field:
                    if manual_cn:
                        from .store import preview_path
                        cn_inventory = read(self.root/'cn-inventory.json')
                        saved = read(preview_path(self.root, cfg), {})
                        if (saved.get('fullCritPriority') and saved.get('networkPreview') and saved.get('inventory') == digest(cn_inventory)
                                and saved.get('configuration') == request['configuration'] and saved.get('rules') == request['rules']):
                            cn_plan = saved
                    network_plan = cn_plan or preview(self.root, wiki_path(), snapshot=pinned, reuse_allocations=manual_cn)
                    if network_plan['issues']:
                        return self.round_fail('配装待复核 · '+' / '.join(network_plan['issues']))
                    if source == 'cn_export':
                        if cn_inventory is None:
                            cn_inventory = read(self.root/'cn-inventory.json')
                        cn_plan = network_plan
            scanner = None
            if interactive or (cfg['run_scanner'] and (not network or mark or field)):
                from time import time
                from .scanner_status import scanner_status
                ready, reason = scanner_status(self.ctx, self.account)
                if not ready:
                    return self.round_fail(reason)
                scanner = self.ctx.run_context.get_application('inventory_scan',self.account,'one_dragon' if interactive else self.group)
                if not scanner.config.scan_drive_disk:
                    return self.round_fail('请在仓库扫描器中启用驱动盘')
                if field and cn_plan is not None and not mark:
                    from .equip_targets import equip_cn_targets
                    from .store import equipment_names
                    wiki = wiki_path()
                    names = equipment_names(read(wiki, {})) | cfg.get('aliases', {})
                    return equip_cn_targets(self, scanner, cfg, cn_plan, cn_inventory, field, names, wiki, request)
                if mark and cn_plan is not None:
                    from .incremental_marks import daily, FILE as CHECKPOINT
                    delta = daily(self, scanner, cn_inventory, cn_plan, pinned, wiki_path(), field)
                    if delta is not None:
                        return delta
                    (self.root/CHECKPOINT).unlink(missing_ok=True)
                scan_started = time()
                exports = Path(os_utils.get_work_dir())/'.debug/inventory_exports'
                before = {p:p.stat().st_mtime_ns for p in exports.glob('inventory_*.json')}
                if cn_inventory is None and (mark or field) and any(str(key).startswith('cn-uid/') for key in cfg['pins']):
                    from .cn_export import FILE, refresh
                    def current_account():
                        if self.ctx.current_instance_idx != self.account or not self.ctx.run_context.is_context_running:
                            raise ValueError('账号已切换或任务已停止')
                    refresh(self.root, current_account, showcase_uid=cfg.get('showcaseUid', ''), wiki=read(wiki_path(), {}))
                    cn_inventory = read(self.root/FILE)
                # Suppress the scanner's optional browser launch for this invocation only.
                from unittest.mock import patch
                from .scan_marks import capture_marks
                try:
                    with capture_marks(scanner, self.root) as text_cache, patch.object(scanner,'_open_export',lambda path: None):
                        result = checked_scan(scanner, self.guard_game) if interactive else scanner.execute()
                    if self.ctx.current_instance_idx != self.account:
                        return self.round_fail('当前账号已改变')
                    if not result.success:
                        raise ValueError(getattr(result, 'status', None) or '扫描中断')
                    if interactive:
                        self.guard_game()
                except Exception:
                    if self.ctx.current_instance_idx == self.account:
                        from .scan_observations import save_interrupted_scan
                        save_interrupted_scan(self.root, scanner, scan_started)
                    raise
                fresh = [p for p in exports.glob('inventory_*.json') if before.get(p)!=p.stat().st_mtime_ns]
                if len(fresh)!=1:
                    return self.round_fail('本轮扫描文件无法唯一识别')
                from .marks import reconcile_scan
                from .salvage_audit import native_run, observe_native, reconcile
                observe_native(self.root, self.account, native_run(Path(os_utils.get_work_dir()), self.account), scan_started)
                from .store import equipment_names
                names = equipment_names(read(wiki_path(), {}))
                names.update(cfg.get('aliases', {}))
                import_inventory(self.root, fresh[0], readings=scanner.ocr_worker.scanned_discs, cn_export=cn_inventory, names=names)
                if text_cache is not None:
                    text_cache.finish()
                snapshot = read(self.root/'inventory.json')
                reconcile(self.root,snapshot)
                reconcile_scan(self.root, snapshot, names)
                # The host advances to the next task even if planning or marking fails.
                if not interactive:
                    from .dismantle import note_existing_marks
                    note_existing_marks(self, snapshot, scan_started)
            if mark and cn_plan is not None:
                from .service import reassign_discards
                cn_plan = reassign_discards(self.root, wiki_path(), pinned, cn_plan, snapshot)
            from .store import snapshot_path
            if scanner is None and not snapshot_path(self.root, cfg).is_file():
                return self.round_fail('请先在仓库页读取库存')
            report = preview(self.root,wiki_path(),snapshot=pinned, verified_game=scanner is not None, cn_plan=cn_plan, field=field)
            if scan_only:
                resumed = f' · 复用 {text_cache.reused} 次词条识别' if text_cache and text_cache.reused else ''
                return self.round_success(f"已扫描 {len(snapshot['discs'])} 张驱动盘{resumed} · 预览已保存")
            if mark:
                from .salvage_audit import needs_review
                if needs_review(read(self.root/'dismantle-result.json', {})):
                    return self.round_fail('请先到「设置 → 库存记录」核对上次的盘')
            if self.group == MANUAL_GROUP:
                self.guard_game()
                try:
                    verify_selection(request, cfg, report, snapshot, names=names)
                except SelectionChanged as change:
                    return self.round_fail(str(change), data=dict(confirmed=request, revised=change.current, targets=change.targets))
            elif report['issues']:
                return self.round_fail('配装待复核 · '+' / '.join(report['issues']))
            if field and scanner is None:
                return self.round_fail('应用出场方案需要本轮重新扫描')
            messages = []
            if mark:
                if scanner is None:
                    return self.round_fail('请开启本轮重新扫描后再标记')
                from .live import Marker
                from .dismantle import handoff
                marker = Marker(self, scanner, report, readings=scanner.ocr_worker.scanned_discs)
                if cn_plan is not None:
                    from .incremental_marks import check_file
                    marker.guard_export = lambda: check_file(cn_inventory)
                    marker.guard_export()
                marker.batch = handoff(self, report)
                observed = marker.run()
                if marker.batch:
                    handoff(self, report, observed, batch=marker.batch)
                messages.append('标记完成')
            if field:
                from .loadout import apply_loadout
                from .equip_ui import EquipUI
                receipt = apply_loadout(self.root,cfg,report,snapshot,EquipUI(self,scanner,wiki_path()),name=field,names=names)
                messages.append(f"{receipt['profile']} · 已复读 {len(receipt['verified'])} 个号位")
            if mark and cn_plan is not None:
                from .incremental_marks import checkpoint
                checkpoint(self.root, cn_inventory, cn_plan, snapshot, report, marker)
            return self.round_success(' / '.join(messages) if messages else '预览 · '+str(report['counts']))
        except (KeyError,ValueError,OSError,TypeError) as exc:
            return self.round_fail(str(exc))
