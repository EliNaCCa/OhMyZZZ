"""Navigate through the game's three warehouse entry screens, one observed page at a time."""
import re
import time
from types import SimpleNamespace


def warehouse_header(ocr, frame):
    tokens = [text.replace(' ', '') for text, boxes in (ocr.run_ocr(frame[:200, :1250]) or {}).items()
              if any(box.confidence >= .85 for box in boxes)]
    text = ''.join(tokens)
    count = re.search(r'驱动仓库[^0-9]{0,10}([0-9]+)/([0-9]+)', text)
    if count and 0 <= int(count[1]) <= int(count[2]) and int(count[2]) > 0:
        return '驱动仓库', int(count[1])
    if '音擎仓库' in text and '驱动仓库' not in text:
        return '音擎仓库', None
    return '', None


def enter_warehouse(operation, guard, *, timeout=16, trace=None):
    from one_dragon.base.screen.screen_utils import is_target_screen
    from one_dragon.base.geometry.point import Point
    from inventory_scan.warehouse_nav import click_warehouse_tab
    ctx = operation.ctx
    def capture():
        guard()
        screen = operation.screenshot()
        if screen is None or screen.shape[:2] != (1080, 1920):
            raise ValueError('驱动仓库需要 1920×1080 游戏画面')
        return screen

    def click(point, **kwargs):
        guard()
        if not ctx.controller.click(point, **kwargs):
            raise ValueError('仓库导航点击未送达')
        return True

    # Keep the scanner's tab-template matching, including its post-click title check.
    tab_operation = SimpleNamespace(ctx=SimpleNamespace(ocr=ctx.ocr, controller=SimpleNamespace(click=click)),
                                    screenshot=capture)
    visited = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        screen = capture()
        page, count = warehouse_header(ctx.ocr, screen)
        if page == '驱动仓库':
            return count
        if not page:
            page = next((p for p in ('菜单', '大世界-普通') if is_target_screen(ctx, screen, screen_name=p)), '')
        if not page or page in visited:
            time.sleep(.3)
            continue
        if page == '音擎仓库':
            if not click_warehouse_tab(tab_operation, 'tab_drive.png', '驱动仓库', label='驱动盘'):
                raise ValueError('音擎仓库未能切换到驱动盘页签')
        elif page == '菜单':
            area = ctx.screen_loader.get_area(page, '底部-仓库')
            if area is None:
                raise ValueError('一条龙缺少菜单中的仓库区域')
            rect = area.rect
            hits = [box for text, boxes in (ctx.ocr.run_ocr(screen[rect.y1:rect.y2, rect.x1:rect.x2]) or {}).items()
                    if text.replace(' ', '') == '仓库' for box in boxes if box.confidence >= .85]
            if len(hits) != 1:
                time.sleep(.3)
                continue
            click(Point(rect.x1 + hits[0].center.x, rect.y1 + hits[0].center.y))
        else:
            area = ctx.screen_loader.get_area(page, '按钮-菜单')
            if area is None:
                raise ValueError('一条龙缺少大世界菜单入口')
            click(area.center, pc_alt=area.pc_alt, gamepad_key=area.gamepad_key)
        visited.add(page)
        if trace is not None:
            trace.append(page)
        time.sleep(.4)
    raise ValueError('仓库导航等待超时，已操作页面为 ' + ('、'.join(trace or sorted(visited)) or '无'))
