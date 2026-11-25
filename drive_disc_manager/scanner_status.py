"""Read the host registration and the scanner's effective disc option without opening the scanner."""


def scanner_status(ctx, account):
    run = ctx.run_context
    if not run.is_app_registered('inventory_scan'):
        return False, '请在一条龙插件管理中安装并启用「仓库扫描器」'
    # inventory_scan 1.4.7 reads its default group even when invoked from another group.
    settings = run.get_config('inventory_scan', account, 'one_dragon')
    enabled = getattr(settings, 'scan_drive_disk', None)
    if type(enabled) is not bool:
        return False, '未读到本账号的驱动盘扫描设置，请核对仓库扫描器版本'
    if not enabled:
        return False, '请在仓库扫描器设置中开启「驱动盘」'
    return True, '已就绪，可由 OhMyZZZ 调用'
