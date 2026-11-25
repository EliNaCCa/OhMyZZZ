"""One confirmed lineup, confined to the calling thread and the host application run."""
from contextvars import ContextVar

from .loadout import target_loadout
from .store import digest


MANUAL_GROUP = 'ohmyzzz_manual_equip'
_request = ContextVar('ohmyzzz_manual_equip', default=None)


def confirmation(account, name, cfg, report, snapshot):
    targets = target_loadout(cfg, report, snapshot, name)
    # Ignore export ordering but include a changed donor in the next preview.
    identity = [{k:t[k] for k in ('profile','agent','slot','fp','observedValues','location')} for t in targets]
    return dict(account=account, name=name, configuration=digest(cfg), selection=digest(identity)), targets


def run_confirmed(ctx, request):
    from .drive_disc_manager_const import APP_ID
    from .disc_probe import interactive_slot, game_guard
    account = request['account']
    run = ctx.run_context
    if ctx.current_instance_idx != account or not run.is_context_stop or not ctx.ready_for_application:
        raise ValueError('请在一条龙空闲且当前账号已就绪时应用配队')
    with interactive_slot() as (session, check_display):
        guard, _ = game_guard(ctx, session, check_display)
        ctx.controller.game_win.active()
        guard()
        token = _request.set((request,guard))
        try:
            result = run.run_application(APP_ID, account, MANUAL_GROUP)
            if (result.app_id, result.instance_idx, result.group_id) != (APP_ID, account, MANUAL_GROUP):
                raise ValueError('一条龙未启动本次换装')
            if result.finish_reason.value != 'COMPLETED':
                detail = getattr(run.last_application_result, 'status', '')
                raise ValueError(detail or '换装已停止，请重新预览')
        finally:
            _request.reset(token)


def manual_request(account):
    item = _request.get()
    if not item or item[0]['account'] != account:
        raise ValueError('本次换装缺少当前账号的确认')
    return item


def verify_selection(request, cfg, report, snapshot):
    current, _ = confirmation(request['account'], request['name'], cfg, report, snapshot)
    if current != request:
        raise ValueError('重扫后的目标盘或配置有变化，已刷新预览，请查看后重新应用')


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
        grid.need_check_game_win = False
        grid.run_record = None
        with patch.object(grid, 'ctx', GridContext()):
            result = grid.execute()
        if not result.success:
            return result
        scanner.ocr_worker.wait_complete()
        guard()
        if scanner._disc_game_total != enter_warehouse(scanner, guard):
            raise ValueError('重扫期间仓库数量变化')
        exported = scanner.export_all_data()
        if not exported.is_success:
            raise ValueError(exported.status or '换装前驱动盘导出失败')
        return result
    finally:
        scanner.ocr_worker.stop()
        scanner.screenshot_cache.reset_all()
