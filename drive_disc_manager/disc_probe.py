"""Read one selected disc from an existing game through OneDragon's warehouse scanner."""
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .store import fingerprint, read, save
from .warehouse import enter_warehouse, warehouse_header


@contextmanager
def interactive_slot():
    import win32api
    import win32con
    import win32file
    import win32ts
    session = win32ts.ProcessIdToSessionId(os.getpid())
    supervisor = Path(os.environ['ProgramData'])/'EliSR-Autorun'
    def check_display():
        if win32ts.WTSQuerySessionInformation(None, session, win32ts.WTSConnectState) != 0:
            raise ValueError('当前桌面会话已断开')
        if (win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)) != (1920,1080):
            raise ValueError('当前游戏桌面需要 1920×1080')
    if not (supervisor/'config.json').is_file():
        check_display()
        yield session, check_display
        return
    settings = read(supervisor/'config.json')
    user = win32ts.WTSQuerySessionInformation(None, session, win32ts.WTSUserName)
    if (win32ts.WTSQuerySessionInformation(None, session, win32ts.WTSClientProtocolType) != 2 or
            user.casefold() != settings['RemoteUser'].casefold()):
        raise ValueError('请在配置的远程游戏桌面打开一条龙')
    check_display()
    lock = win32file.CreateFile(str(supervisor/'state'/'application-use.lock'), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                0, None, win32con.OPEN_ALWAYS, win32con.FILE_ATTRIBUTE_NORMAL, None)
    try:
        import yaml
        # The supervisor uses this default when no executable override is configured.
        march = settings.get('March7Executable')
        source = Path(march).parent/'config.yaml' if march else Path('E:/Program Files/March7thAssistant/config.yaml')
        tasks = yaml.safe_load(source.read_text('utf-8-sig'))['scheduled_tasks']
        starts = [tuple(map(int, task['time'].split(':'))) for task in tasks
                  if task.get('enabled', True) and task.get('trigger_mode', 'time') == 'time']
        def check_slot():
            check_display()
            now = datetime.now()
            for hour, minute in starts:
                start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if any(-300 <= (start + timedelta(days=offset) - now).total_seconds() <= 120 for offset in (-1,0,1)):
                    raise ValueError('临近已启用的业务时段，请稍后再操作驱动盘')
        check_slot()
        yield session, check_slot
    finally:
        lock.Close()


def read_selected(scanner, total, guard, *, target=None):
    from one_dragon.utils import cv2_utils
    from .scan_marks import MarkReader
    if total == 0:
        raise ValueError('驱动仓库为空')
    ctx = scanner.ctx
    area = ctx.screen_loader.get_area('仓库-驱动仓库', '驱动盘属性')
    if area is None:
        raise ValueError('仓库扫描器缺少驱动盘属性区域')
    first = None
    for _ in range(2):
        guard()
        screen = scanner.screenshot()
        if screen is None or screen.shape[:2] != (1080,1920) or warehouse_header(ctx.ocr, screen) != ('驱动仓库', total):
            raise ValueError('复读时仓库标题、数量或画面尺寸变化')
        if target is not None:
            from .scan_copies import selected_position
            if selected_position(screen, target) is None:
                raise ValueError('选中的盘位已变化')
        crop = cv2_utils.crop_image_only(screen, area.rect)
        items = [dict(text=text, confidence=b.confidence, position=(b.x,b.y,b.x+b.w,b.y+b.h))
                 for text, boxes in (ctx.ocr.run_ocr(crop) or {}).items() for b in boxes]
        reader = MarkReader(scanner.drive_disk_scanner.parser, ctx.ocr, screen[985:1070,1050:1920])
        disc = reader.parse_ocr_result(items, crop)
        raw = ''.join(i['text'] for i in items if i['confidence'] >= .85).replace(' ', '').replace('【', '[').replace('】', ']')
        slot = re.search(r'\[([1-6])\]', raw)
        level = re.search(r'(?:等级|Lv[.]?)([0-9]{1,2})/(?:9|12|15)(?![0-9])', raw, re.I)
        if (not disc or disc.get('setKey') in (None, '', 'Unknown') or not slot or str(disc['slotKey']) != slot[1]
                or not level or disc['level'] != int(level[1]) or not disc.get('observedValues')
                or 'unknown' in (disc['markState'],disc['lockState'])):
            raise ValueError('当前盘的主副词条、锁定或弃置状态未读清')
        reading = {k:disc[k] for k in ('setKey','slotKey','level','rarity','mainStatKey','substats','observedValues','markState','lockState','ownerState','location')}
        if 'mainStatValue' in disc:
            reading['mainStatValue'] = disc['mainStatValue']
        reading['fingerprint'] = fingerprint(disc)
        guard()
        if first is not None:
            if reading != first:
                raise ValueError('两次单盘读数不一致')
            return reading
        first = reading
        time.sleep(.35)


def game_guard(ctx, session, check_display):
    import psutil
    import win32gui
    import win32process
    import win32ts
    controller = ctx.controller
    configured = Path(ctx.game_account_config.game_path)
    if not controller.init_game_win():
        raise ValueError('请先在当前桌面打开绝区零并进入大世界')
    window = controller.game_win
    hwnd = window.get_hwnd()
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    game = psutil.Process(pid)
    if (configured.name.casefold() != 'zenlesszonezero.exe' or Path(game.exe()).resolve() != configured.resolve()
            or win32ts.ProcessIdToSessionId(pid) != session):
        raise ValueError('游戏窗口与本账号配置或会话不一致')

    def guard():
        check_display()
        if (not game.is_running() or hwnd != win32gui.GetForegroundWindow() or hwnd != window.get_hwnd()
                or pid != win32process.GetWindowThreadProcessId(hwnd)[1]):
            raise ValueError('游戏已退出或失去焦点')
    return guard, pid


def probe(ctx, root, account, *, recent_count=None):
    from .maintenance import exclusive
    record = dict(account=account, started=time.time(), route=[])
    def idle():
        if not ctx.run_context.is_context_stop or ctx.current_instance_idx != account:
            raise ValueError('一条龙任务或当前账号已变化')
    with exclusive(root.parent/'_maintenance'):
        try:
            idle()
            with interactive_slot() as (session, check_display):
                record['session'] = session
                check_game, record['game'] = game_guard(ctx, session, check_display)
                def guard():
                    idle()
                    check_game()
                scanner = ctx.run_context.get_application('inventory_scan', account, 'one_dragon')
                scanner._ensure_screen_areas()
                ctx.controller.game_win.active()
                if recent_count is not None:
                    from .recent_discs import leave_disc_detail
                    leave_disc_detail(scanner, guard)
                total = enter_warehouse(scanner, guard, trace=record['route'])
                record['warehouseCount'] = total
                if recent_count is None:
                    record['disc'] = read_selected(scanner, total, guard)
                else:
                    from .recent_discs import read_front_row
                    record['discs'] = read_front_row(scanner, total, guard, recent_count)
                guard()
                record['status'] = 'verified'
        except Exception as exc:
            record.update(status='failed', error=str(exc))
        record['finished'] = time.time()
        if recent_count is None:
            save(root/'single-disc.json', record)
        elif record['status'] == 'verified':
            save(root/'recent-discs.json', record)
        return record
