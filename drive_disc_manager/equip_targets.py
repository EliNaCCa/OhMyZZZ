"""Find the selected CN discs in the warehouse. Never publish a partial inventory."""
from collections import Counter
from time import sleep

from .inventory_comparison import attribute_key
from .loadout import apply_checked_targets, target_loadout
from .manual_equip import SelectionChanged, guarded_grid, target_confirmation, verify_targets
from .stat_reading import checked_values
from .store import equipment_keys, fingerprint, roll_base


def reading_identity(disc):
    if disc:
        return (fingerprint(disc), checked_values(disc, 1), disc.get('observedGrade'), disc.get('markState'),
                disc.get('lockState'), disc.get('ownerState'), disc.get('location'))


def match_targets(exported, targets, names, observations):
    reverse = equipment_keys(names)
    base = roll_base(exported)
    discs = {d['id']: d for d in exported['discs']}
    keys = {uid: attribute_key(d, base, names, reverse) for uid, d in discs.items()}
    counts = Counter(keys.values())
    waiting = {}
    for target in targets:
        uid = target['selectionFp'].removeprefix('cn-uid/')
        key = keys[uid]
        if counts[key] != 1 or key in waiting:
            raise ValueError(f"{target['agent']} 的 {target['slot']} 号位有同属性盘，请在游戏里选择")
        waiting[key] = target
    found = {}
    for disc, repeat in observations:
        key = attribute_key(disc, 1, names, reverse)
        target = waiting.get(key)
        if target is None:
            continue
        values = checked_values(disc, 1)
        if values != target['observedValues'] or disc.get('observedGrade') != disc['rarity']:
            continue
        # inventory_scan assigns a new local id every time its parser reads a
        # frame. Compare the actual item, numeric readings and controls.
        if reading_identity(repeat()) != reading_identity(disc):
            continue
        if disc.get('markState') == 'marked':
            raise ValueError(f"{target['profile']} 的 {target['slot']} 号盘已标记弃置")
        state, wearer = disc.get('ownerState'), disc.get('location', '')
        if (disc.get('markState') != 'unmarked' or disc.get('trash') is not False or
                not ((state == 'bag' and not wearer) or (state == 'worn' and wearer))):
            continue
        found[target['selectionFp']] = dict(target, fp=fingerprint(disc), set=disc['setKey'], location=wearer,
                                            observedValues=values, ownerState=state)
        del waiting[key]
        if not waiting:
            return [found[t['selectionFp']] for t in targets]
    missing = '、'.join(f"{t['profile']} {t['slot']}号位" for t in waiting.values())
    raise ValueError('仓库里还有方案用盘没核实 · '+missing)


def warehouse_observations(scanner, total, guard, *, by_acquisition=False):
    from one_dragon.utils import cv2_utils
    from .scan_copies import selected_position, scroll_thumb, viewport_stamp
    from .scan_marks import MarkReader
    from .warehouse import enter_warehouse, warehouse_header

    ctx = scanner.ctx
    scanner._ensure_screen_areas()
    observed_total = enter_warehouse(scanner, guard)
    if observed_total != total:
        raise ValueError(f'游戏仓库有 {observed_total} 张，国服导出有 {total} 张，请更新导出文件')
    if by_acquisition:
        from .warehouse_sort import newest_first
        newest_first(scanner, total, guard)
    area = ctx.screen_loader.get_area('仓库-驱动仓库', '驱动盘属性')
    if area is None:
        raise ValueError('扫描器缺少驱动盘属性区域')

    def frame():
        guard()
        screen = scanner.screenshot()
        if (screen is None or screen.shape[:2] != (1080, 1920) or
                warehouse_header(ctx.ocr, screen) != ('驱动仓库', total)):
            raise ValueError('仓库画面或盘数已变化')
        return screen

    def read_disc(screen, point, page):
        selected = selected_position(screen, point)
        if selected is None:
            return None
        crop = cv2_utils.crop_image_only(screen, area.rect)
        items = [dict(text=text, confidence=b.confidence, position=(b.x,b.y,b.x+b.w,b.y+b.h))
                 for text, boxes in (ctx.ocr.run_ocr(crop) or {}).items() for b in boxes]
        cell = (page, *selected)
        thumb = scroll_thumb(screen)
        if thumb:
            cell += (viewport_stamp(screen, selected[0], thumb), thumb)
        return MarkReader(scanner.drive_disk_scanner.parser, ctx.ocr, screen[985:1070,1050:1920], cell).parse_ocr_result(items, crop)

    with guarded_grid(scanner.drive_disk_scanner, guard) as grid:
        frame()
        grid.ctx.controller.scroll(-120, grid._scroll_list_center())
        sleep(.5)
        unchanged = 0
        for page in range(total + 1):
            screen = frame()
            points = grid._detect_points(screen)
            if not points:
                raise ValueError('仓库盘位没读清')
            grid.grid_rows, _note = grid._resolve_grid(points)
            if not any(grid.grid_rows):
                raise ValueError('仓库盘位没读清')
            for row, line in enumerate(grid.grid_rows):
                for col, point in enumerate(line):
                    frame()
                    if not grid._ensure_selected(point, row, col):
                        continue
                    grid._stable_screenshot()
                    disc = read_disc(frame(), point, page)
                    if disc and disc.get('observedValues') and disc.get('observedGrade'):
                        yield disc, lambda p=point, n=page: read_disc(frame(), p, n)
            before = frame()
            grid.ctx.controller.scroll(3, grid._scroll_list_center())
            sleep(.55)
            after = frame()
            same = (scroll_thumb(before) == scroll_thumb(after) and
                    (before[240:860:8,110:1310:8] == after[240:860:8,110:1310:8]).mean() > .995)
            unchanged = unchanged + 1 if same else 0
            if unchanged >= 2:
                return


def equip_cn_targets(app, scanner, cfg, report, exported, field, names, wiki, request=None):
    from contextlib import closing
    from .equip_ui import EquipUI
    ui = EquipUI(app, scanner, wiki)
    targets = target_loadout(cfg, report, exported, field, preview_only=True, names=names)
    with closing(warehouse_observations(scanner, len(exported['discs']), ui.guard)) as observations:
        checked = match_targets(exported, targets, names, observations)
    ui.guard()
    if request is not None:
        current = target_confirmation(app.account, field, cfg, report, checked)
        try:
            verify_targets(request, current, checked)
        except SelectionChanged as change:
            return app.round_fail(str(change), data=dict(confirmed=request, revised=current, targets=checked))
    receipt = apply_checked_targets(app.root, report, field, checked, ui)
    return app.round_success(f"{field} · 已复读 {len(receipt['verified'])} 个号位")
