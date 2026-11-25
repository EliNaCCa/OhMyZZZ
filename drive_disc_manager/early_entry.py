"""Run only OhMyZZZ in the game session retained by the successful early farm."""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time


def verify_game(args):
    import psutil
    import win32ts

    game = psutil.Process(args.game_pid)
    start = (args.game_ticks - 621355968000000000) / 10_000_000
    if (win32ts.ProcessIdToSessionId(os.getpid()) != args.session or args.session <= 0
            or win32ts.ProcessIdToSessionId(game.pid) != args.session
            or game.name().casefold() != 'zenlesszonezero.exe' or abs(game.create_time() - start) > .001):
        raise ValueError('游戏已退出或切换了远程会话')
    if datetime.now(timezone(timedelta(hours=8))).date().isoformat() != args.date:
        raise ValueError('早场已跨业务日')


def run(ctx, args, check_game=verify_game):
    from drive_disc_manager.drive_disc_manager_const import APP_ID
    from drive_disc_manager.store import config, read, save

    idx = ctx.current_instance_idx
    root = ctx.get_plugin_data_dir(APP_ID) / str(idx)
    cfg = config(root)
    if cfg.get('afterEarly') is not True:
        return 0
    path = root / 'early-result.json'
    previous = read(path, {})
    if previous.get('run') == args.run:
        return 0 if previous.get('status') in ('COMPLETED', 'daily') else 2
    record = dict(run=args.run, date=args.date, account=idx, started=time(), status='running')
    save(path, record)
    try:
        check_game(args)
        ctx.init()
        if not ctx.ready_for_application or ctx.current_instance_idx != idx:
            raise ValueError('一条龙初始化失败或当前账号已切换')
        od = ctx.one_dragon_config
        if od.instance_run != '仅运行当前' and [item.idx for item in od.instance_list_in_od] != [idx]:
            raise ValueError('多账号轮转后无法确认游戏账号，请使用日常列表入口')
        group = ctx.app_group_manager.get_one_dragon_group_config(idx)
        if any(item.app_id == APP_ID and item.enabled for item in group.app_list):
            record.update(status='daily', reason='由一条龙日常列表管理')
            return 0
        if not cfg['run_scanner']:
            raise ValueError('早场自动维护需要启用本轮扫描')
        check_game(args)
        result = ctx.run_context.run_application(APP_ID, idx, 'one_dragon')
        if (result.app_id, result.instance_idx, result.group_id) != (APP_ID, idx, 'one_dragon'):
            raise ValueError('运行回执与本次账号或任务不符')
        detail = ctx.run_context.last_application_result
        record.update(status=result.finish_reason.value, reason=str(getattr(detail, 'status', '') or ''))
        return 0 if record['status'] == 'COMPLETED' else 2
    except Exception as exc:
        record.update(status='FAILED', reason=str(exc))
        return 2
    finally:
        save(path, dict(record, finished=time()))


def main():
    parser = argparse.ArgumentParser()
    for key in ('run', 'date', 'game-pid', 'game-ticks', 'session'):
        parser.add_argument('--' + key, required=True, type=str if key in ('run', 'date') else int)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{32}', args.run):
        parser.error('invalid early run identifier')
    home = Path.cwd()
    sys.path[:0] = [str(home / 'src'), str(home / 'plugins')]
    os.environ.setdefault('VGAMEPAD_SKIP_VIGEMBUS_INSTALL', 'true')
    from one_dragon.utils import os_utils
    os_utils.set_work_dir(str(home))
    from zzz_od.context.zzz_context import ZContext
    ctx = ZContext()
    try:
        return run(ctx, args)
    finally:
        ctx.after_app_shutdown()


if __name__ == '__main__':
    sys.exit(main())
