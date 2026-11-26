"""Describe when the current account's native daily task can pick up disc marks."""
from .drive_disc_manager_const import APP_ID


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


def describe_order(group, native, cfg):
    enabled = [row.app_id for row in group.app_list if row.enabled]
    daily = APP_ID in enabled
    entry = '已加入日常' if daily else '未加入日常'
    mode = '扫描并标记' if cfg['applyMarkers'] else ('扫描并预览' if cfg['run_scanner'] else '使用已有库存预览')
    parts = [f'{entry} · {mode}']
    if native is None or NATIVE_ID not in enabled:
        parts.append('内置拆解未启用')
    else:
        scope = native.dismantle_level + ('、已弃置盘' if native.dismantle_abandon else '')
        parts.append(f'内置拆解 · {scope}')
        if native.dismantle_level == 'S及以下':
            parts.append('当前品级包含未弃置的 S 盘，自动标记将暂停。')
        elif native.dismantle_abandon and cfg['applyMarkers'] and daily:
            if enabled.index(APP_ID) > enabled.index(NATIVE_ID):
                parts.append('新标记将在下一轮拆解。把 OhMyZZZ 移到拆解前可在本轮处理。')
            else:
                parts.append('本轮先标记，再由内置任务拆解。')
    return '\n'.join(parts)


def account_order(ctx, account, cfg):
    group, native = group_settings(ctx, account)
    return describe_order(group, native, cfg)
