"""One confirmed lineup, confined to the calling thread and the host application run."""
from contextvars import ContextVar

from .loadout import target_loadout
from .store import digest


MANUAL_GROUP = 'ohmyzzz_manual_equip'
SCAN_GROUP = 'ohmyzzz_manual_scan'
_request = ContextVar('ohmyzzz_manual_equip', default=None)


class SelectionChanged(ValueError):
    def __init__(self, original, current, targets):
        super().__init__('配队用盘有变化，请查看后重新应用')
        self.original = original
        self.current = current
        self.targets = targets


def selection_digest(targets, unknown_owners=()):
    identity = []
    for target in targets:
        entry = {k:target[k] for k in ('profile','agent','slot','selectionFp','location','ownerState')}
        if target['selectionFp'].startswith('cn-uid/'):
            entry.update(level=target['level'], rarity=target['rarity'], main=target['main'], set=target['selectionSet'])
            if target['selectionFp'] in unknown_owners and ((entry['ownerState'], entry['location']) in
                    (('bag', ''), ('worn', entry['agent']))):
                entry.update(ownerState='unknown', location='')
        # OCR writes 19.0 and the reader exports 19. checked_values bounds both to 99999.
        entry['values'] = {group:{k:float(v) for k, v in stats.items()}
                           for group, stats in target['observedValues'].items()}
        identity.append(entry)
    return digest(identity)


def confirmation(account, name, cfg, report, snapshot, *, preview_only=False, names=None):
    targets = target_loadout(cfg, report, snapshot, name, preview_only=preview_only, names=names)
    unknown = [t['selectionFp'] for t in targets if t['selectionFp'].startswith('cn-uid/') and t['ownerState'] == 'unknown']
    return dict(account=account, name=name, configuration=digest(cfg), rules=report.get('rules'),
                selection=selection_digest(targets), unknownOwners=unknown), targets


def run_confirmed(ctx, request):
    return _run_interactive(ctx, request, MANUAL_GROUP, '换装')


def run_scan_preview(ctx, account):
    return _run_interactive(ctx, {'account': account}, SCAN_GROUP, '扫描预览')


def _run_interactive(ctx, request, group, action):
    from .drive_disc_manager_const import APP_ID
    from .disc_probe import interactive_slot, game_guard
    account = request['account']
    run = ctx.run_context
    if ctx.current_instance_idx != account or not run.is_context_stop or not ctx.ready_for_application:
        raise ValueError(f'请在一条龙空闲且当前账号已就绪时开始{action}')
    with interactive_slot() as (session, check_display):
        guard, _ = game_guard(ctx, session, check_display)
        ctx.controller.game_win.active()
        guard()
        token = _request.set((group, request, guard))
        try:
            result = run.run_application(APP_ID, account, group)
            if (result.app_id, result.instance_idx, result.group_id) != (APP_ID, account, group):
                raise ValueError(f'一条龙未启动本次{action}')
            if result.finish_reason.value != 'COMPLETED':
                outcome = run.last_application_result
                change = getattr(outcome, 'data', None)
                if (result.finish_reason.value == 'FAILED' and group == MANUAL_GROUP
                        and isinstance(change, dict) and change.get('confirmed') is request):
                    raise SelectionChanged(request, change['revised'], change['targets'])
                detail = getattr(outcome, 'status', '')
                raise ValueError(detail or f'{action}已停止，请重新预览')
        finally:
            _request.reset(token)


def manual_request(account, group):
    item = _request.get()
    if not item or item[0] != group or item[1]['account'] != account:
        raise ValueError('未从当前账号配置页发起此操作')
    return item[1], item[2]


def verify_selection(request, cfg, report, snapshot, *, names=None):
    if request['configuration'] != digest(cfg) or request['rules'] != report.get('rules'):
        raise ValueError('配队配置或规则已更新，请重新计算')
    current, targets = confirmation(request['account'], request['name'], cfg, report, snapshot, names=names)
    # Reading an unassigned disc in the bag or already on its recipient needs
    # no new decision. Borrowing from a different agent still changes the plan.
    if selection_digest(targets, request.get('unknownOwners', ())) != request['selection']:
        raise SelectionChanged(request, current, targets)


def checked_scan(scanner, guard):
    """Bind navigation and the disc grid to the already validated foreground client."""
    from unittest.mock import patch
    from .warehouse import enter_warehouse
    grid = scanner.drive_disk_scanner
    ctx = grid.ctx

    class GridController:
        def __getattr__(self, key):
            guard()
            return getattr(ctx.controller, key)

    class GridContext:
        controller = GridController()

        def __getattr__(self, key):
            return getattr(ctx, key)

    guard()
    scanner._ensure_screen_areas()
    scanner._disc_game_total = enter_warehouse(scanner, guard)
    scanner.screenshot_cache.reset_all()
    scanner.ocr_worker.reset()
    scanner.ocr_worker.start()
    try:
        with (patch.object(grid, 'ctx', GridContext()), patch.object(grid, 'need_check_game_win', False),
              patch.object(grid, 'run_record', None)):
            result = grid.execute()
        if not result.success:
            return result
        scanner.ocr_worker.wait_complete()
        guard()
        if scanner._disc_game_total != enter_warehouse(scanner, guard):
            raise ValueError('重扫期间仓库数量变化')
        exported = scanner.export_all_data()
        if not exported.is_success:
            raise ValueError(exported.status or '驱动盘扫描导出失败')
        return result
    finally:
        scanner.ocr_worker.stop()
        scanner.screenshot_cache.reset_all()
