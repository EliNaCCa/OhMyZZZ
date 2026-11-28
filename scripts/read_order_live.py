"""Read the player's warehouse in acquisition order from the SR_Eli session."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path

host = Path('F:/OneDragon')
root = host/'plugin_data/drive_disc_manager/1'
run = Path(sys.argv[2])
os.chdir(host)
sys.path[:0] = [str(host/'plugins'), str(host/'src')]
os.environ['VGAMEPAD_SKIP_VIGEMBUS_INSTALL'] = 'true'
ctypes.windll.user32.SetProcessDPIAware()
from drive_disc_manager.store import read, save


def main():
    from drive_disc_manager.disc_probe import game_guard, interactive_slot
    from drive_disc_manager.disc_order import observed_ranks
    from drive_disc_manager.order_capture import capture
    from drive_disc_manager.maintenance import exclusive
    from drive_disc_manager.warehouse import enter_warehouse, warehouse_header
    from one_dragon.base.screen.screen_utils import is_target_screen
    from one_dragon.utils import os_utils
    from zzz_od.context.zzz_context import ZContext
    from zzz_od.operation.enter_game.enter_game import EnterGame

    os_utils.set_work_dir(str(host))
    with interactive_slot() as (session, check_display), exclusive(root.parent/'_maintenance'):
        ctx = ZContext()
        ctx._start_btn_listener = lambda:None
        ctx.init()
        if not ctx.ready_for_application or ctx.current_instance_idx != 1:
            raise ValueError('一条龙账号 01 未就绪')
        guard,pid = game_guard(ctx, session, check_display)
        if pid != int(sys.argv[1]):
            raise ValueError('游戏已重启')
        ctx.controller.game_win.active()
        scanner = ctx.run_context.get_application('inventory_scan', 1, 'one_dragon')
        scanner._ensure_screen_areas()
        login = EnterGame(ctx)
        deadline = time.monotonic()+150
        while time.monotonic() < deadline:
            guard()
            frame = scanner.screenshot()
            if warehouse_header(ctx.ocr,frame)[0] or any(is_target_screen(ctx,frame,screen_name=s) for s in ('菜单','大世界-普通')):
                break
            label,_ = login.check_enter_click_status_text(frame, include_enter_click=True)
            if label == '点击进入游戏':
                login.round_by_click_area('打开游戏',label)
            else:
                login.check_screen_to_interact(frame)
            time.sleep(1.5)
        total = enter_warehouse(scanner,guard)
        save(run/'status.json', dict(status='reading', total=total, readings=0))
        seed = {}
        if len(sys.argv) == 4:
            previous = Path(sys.argv[3]).resolve()
            if previous.parent != run.resolve().parent or read(previous/'status.json').get('total') != total:
                raise ValueError('上次扫描的账号或仓库盘数不同')
            if (previous/'progress.json').is_file():
                seed = {int(k):v for k,v in read(previous/'progress.json')['rows'].items()}
            else:
                seed = observed_ranks(previous,total)
        return capture(scanner,total,guard,run,root,seed)


try:
    save(run/'status.json', dict(status='opening'))
    result = main()
except BaseException as exc:
    import traceback
    (run/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
    result = dict(status='failed', message=str(exc))
save(run/'result.json',result)
