"""Describe when the current account's native daily task can pick up disc marks."""



NATIVE_ID = 'drive_disc_dismantle'


def group_settings(ctx, account, group_id='one_dragon'):
    manager = ctx.app_group_manager
    group = (manager.get_one_dragon_group_config(account) if group_id == 'one_dragon'
             else manager.get_group_config(account, group_id))
    if group is None:
        raise ValueError('未读取到本账号的任务组 · '+group_id)
    native = None
    if any(row.app_id == NATIVE_ID and row.enabled for row in group.app_list):
        # DriveDiscDismantleApp reads DEFAULT_GROUP_ID even when launched from a custom group.
        native = ctx.run_context.get_config(NATIVE_ID, account, 'one_dragon')
        if native is None or native.dismantle_level not in ('A及以下','B','S及以下') or type(native.dismantle_abandon) is not bool:
            raise ValueError('请在一条龙中核对内置拆解的品级和「全选已弃置」')
    return group, native


def marker_conflict(ctx, account, current_group):
    group_ids = dict.fromkeys(['one_dragon', current_group, *ctx.app_group_manager.get_group_list(account)])
    for group_id in group_ids:
        group, native = group_settings(ctx, account, group_id)
        if any(row.app_id == 'disc_cleanup' and row.enabled for row in group.app_list):
            return '同账号应用组已启用旧驱动盘清理器 · '+group_id
        if native and native.dismantle_level == 'S及以下':
            return '内置拆解还会选择未弃置的 S 级盘 · '+group_id
    return ''


def following_native(ctx, account, group_id):
    """Return the native task only when this group run still has it ahead."""
    from .drive_disc_manager_const import APP_ID
    run = ctx.run_context
    root_app = getattr(getattr(run, 'current_application', None), 'app_id', None)
    if (root_app not in ('one_dragon', 'group_' + group_id) or
            run.current_app_id != APP_ID or run.current_group_id != group_id):
        return None
    group, native = group_settings(ctx, account, group_id)
    enabled = [item.app_id for item in group.app_list if item.enabled]
    if not native or enabled.count(APP_ID) != 1 or enabled.count(NATIVE_ID) != 1:
        return None
    if enabled.index(APP_ID) >= enabled.index(NATIVE_ID):
        return None
    record = run.get_run_record(app_id=NATIVE_ID, instance_idx=account)
    if record is None or record.run_status_under_now == record.STATUS_SUCCESS:
        return None
    return native, record.get_current_dt()
