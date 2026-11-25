"""Describe when the current account's native daily task can pick up disc marks."""
from .drive_disc_manager_const import APP_ID


NATIVE_ID = 'drive_disc_dismantle'


def describe_order(group, native, cfg):
    enabled = [row.app_id for row in group.app_list if row.enabled]
    daily = APP_ID in enabled
    if daily:
        entry = '日常任务列表' + ('（早场钩子自动跳过）' if cfg['afterEarly'] else '')
    else:
        entry = '早场刷后' if cfg['afterEarly'] else '手动运行'
    parts = [f'入口 · {entry}　｜　自动标记 · ' + ('开' if cfg['applyMarkers'] else '关')]
    if native is None or NATIVE_ID not in enabled:
        parts.append('内置「驱动盘拆解」未启用。')
        return '\n'.join(parts)
    grade = native.dismantle_level
    marked = native.dismantle_abandon
    parts.append(f'内置拆解 · {grade}　｜　全选已弃置 · ' + ('开' if marked else '关'))
    if grade == 'S及以下':
        parts.append('当前范围包含未标弃置的 S 盘，OhMyZZZ 会在自动标记前停止。')
        return '\n'.join(parts)
    if not marked:
        parts.append('当前只按品级拆解。要纳入旧标记和新标记，请在内置任务中开启「全选已弃置」。')
    elif not cfg['applyMarkers']:
        parts.append('现有弃置标记会在内置拆解下次执行时纳入。')
    elif not daily:
        parts.append('早场刷后标记的盘，等内置拆解下次实际运行时处理。' if cfg['afterEarly'] else
                     '手动标记完成后，内置拆解下次执行时会纳入这些盘。')
    elif enabled.index(APP_ID) > enabled.index(NATIVE_ID):
        parts.append('内置拆解排在标记前，新标记等下次拆解。要在同一轮处理，请把 OhMyZZZ 移到拆解前。')
    else:
        parts.append('日常先标记再拆解。两项本轮均执行时，旧标记和新标记可一起处理。')
    return '\n'.join(parts)


def account_order(ctx, account, cfg):
    group = ctx.app_group_manager.get_one_dragon_group_config(account)
    native = None
    if any(row.app_id == NATIVE_ID and row.enabled for row in group.app_list):
        native = ctx.run_context.get_config(NATIVE_ID, account, 'one_dragon')
        if native is None:
            raise ValueError('未读取到内置拆解配置')
    return describe_order(group, native, cfg)
