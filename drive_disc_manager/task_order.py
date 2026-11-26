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
