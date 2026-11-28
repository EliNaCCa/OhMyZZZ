"""Read the holes of an acquisition-order warehouse pass, locating each screen by the discs already known.

A screen is never located from card images or from the scrollbar: the scrollbar's scale changes with the size
of the warehouse, and a card's artwork changes while it is selected. Discs read on a screen are matched against
readings that already have a position, so a screen with nothing recognisable on it stops the pass instead of
placing discs by guesswork. The clicks, the selection check and the page scroll are the ones warehouse_observations
uses, which have read the whole warehouse on a live client.
"""
import time

import cv2

from .order_positions import locate
from .store import save


class ScreenUnlocated(ValueError):
    """No disc on the screen could be matched to a known position."""


def usable(disc):
    """A reading whose stat values were checked against what the game can show for that disc."""
    return bool(disc and disc.get('observedValues') and disc.get('observedGrade'))


def placed(disc):
    """A reading that names every stat of the disc, whether or not a value could be checked.

    The order record only needs set, slot, level, main stat and the substat lines with their roll counts to
    match a disc against the network inventory; the stat values play no part there. A reading taken in this
    way keeps an empty observedValues, so every later check against a physical disc still rejects it.
    """
    if not disc or not disc.get('observedGrade') or disc.get('setKey') in (None, '', 'Unknown'):
        return False
    if str(disc.get('slotKey')) not in ('1', '2', '3', '4', '5', '6') or type(disc.get('level')) is not int:
        return False
    from .stat_reading import valid_slot_main
    from .optimizer import STATS
    subs = disc.get('substats')
    if not isinstance(subs, list) or len(subs) not in (3, 4) or not valid_slot_main(disc):
        return False
    keys = [s.get('key') for s in subs]
    if len(set(keys)) != len(keys) or disc['mainStatKey'] in keys:
        return False
    return all(k in STATS and type(s.get('upgrades')) is int and 1 <= s['upgrades'] <= 6 for k, s in zip(keys, subs))


def read_disc(scanner, screen, point, page):
    """Parse the disc whose card is selected at point. The same reading warehouse_observations makes."""
    from one_dragon.utils import cv2_utils
    from .scan_cell import selected_card
    from .scan_copies import scroll_thumb, viewport_stamp
    from .scan_marks import MarkReader, reread
    selected = selected_card(screen, point)
    if selected is None:
        return None, None
    ocr = scanner.ctx.ocr
    area = scanner.ctx.screen_loader.get_area('仓库-驱动仓库', '驱动盘属性')
    crop = cv2_utils.crop_image_only(screen, area.rect)
    items = [dict(text=text, confidence=b.confidence, position=(b.x, b.y, b.x+b.w, b.y+b.h))
             for text, boxes in (ocr.run_ocr(crop) or {}).items() for b in boxes]
    cell = (page, *selected)
    thumb = scroll_thumb(screen)
    if thumb:
        cell += (viewport_stamp(screen, selected[0], thumb), thumb)
    reader = MarkReader(scanner.drive_disk_scanner.parser, ocr, screen[985:1070, 1050:1920], cell)
    disc = reader.parse_ocr_result(items, crop)
    if disc and not usable(disc) and placed(disc):
        # The lines are all named but a value was read with too little confidence. Read the panel again
        # enlarged and keep the result only when it names the same lines and passes the same value check.
        disc = reread(reader, ocr, crop, disc) or disc
    return disc, crop


def capture(scanner, total, guard, run, root, seed):
    """Read every position missing from seed, in acquisition order, then finish the account's order scan."""
    from one_dragon.base.geometry.point import Point
    from .disc_order import finish_scan
    from .manual_equip import guarded_grid
    from .scan_copies import scroll_thumb
    from .warehouse import warehouse_header
    from .warehouse_sort import newest_first

    if not newest_first(scanner, total, guard):
        raise ValueError('获得顺序的方向未核实')
    ocr = scanner.ctx.ocr
    scanner._ensure_screen_areas()
    if scanner.ctx.screen_loader.get_area('仓库-驱动仓库', '驱动盘属性') is None:
        raise ValueError('扫描器缺少驱动盘属性区域')
    rows = dict(seed)
    holes = set(range(1, total+1)) - set(rows)
    images = run/'panels'
    images.mkdir(exist_ok=True)
    unread = set()

    def frame():
        guard()
        screen = scanner.screenshot()
        if screen is None or screen.shape[:2] != (1080, 1920) or warehouse_header(ocr, screen) != ('驱动仓库', total):
            raise ValueError('仓库画面或盘数已变化')
        return screen

    def persist(page):
        save(run/'progress.json', dict(account=root.name, total=total, rows=rows, unread=sorted(unread)))
        save(run/'status.json', dict(status='reading', total=total, page=page, ordered=len(rows),
                                     remaining=len(holes), missingStats=sorted(unread)))

    with guarded_grid(scanner.drive_disk_scanner, guard) as grid:
        center = Point(700, 540)
        for _ in range(6):
            grid.ctx.controller.scroll(-160, center)
            time.sleep(.40)
            top = scroll_thumb(frame())
            if total <= 36 or (top and top[1] <= 7):
                break
        else:
            raise ValueError('获得顺序未返回第一张')
        page = 0
        previous = None
        for _ in range(total + 1):
            screen = frame()
            points = grid._detect_points(screen)
            grid.grid_rows, _note = grid._resolve_grid(points)
            cells = [(row*9+col, point) for row, line in enumerate(grid.grid_rows) if line
                     for col, point in enumerate(line) if point is not None and row*9+col < 36]
            if not cells:
                raise ValueError('仓库盘位没读清')

            def open_cell(point, offset):
                """Click a card the way warehouse_observations does; False when the slot is empty."""
                return grid._ensure_selected(point, *divmod(offset, 9))

            # Locate the screen by reading the top row from the left until one disc fixes where the screen starts.
            # Nothing read here is filed yet: a reading may only be stored under a position that discs already
            # known have established. A duplicated disc (two positions vote alike) needs a second reading.
            seen, start = {}, None
            for offset, point in cells:
                if offset >= 9:
                    break
                if open_cell(point, offset):
                    grid._stable_screenshot()
                    disc, _crop = read_disc(scanner, frame(), point, page)
                    if disc:
                        seen[offset] = disc
                        start = locate(seen, rows)
                        if start is not None:
                            break
            if start is None:
                raise ScreenUnlocated(f'第 {page+1} 屏第一行没有对上已知的盘，已停在已读 {len(rows)} 张')
            if page and start <= previous:
                # The list did not move, yet this screen was not the end of the warehouse.
                raise ValueError(f'仓库列表已不再滚动，还有 {len(holes)} 张没读到')
            previous = start
            for offset, point in cells:
                rank = start + offset + 1
                if not 1 <= rank <= total or rank not in holes:
                    continue
                disc, crop = seen.get(offset), None
                if disc is None and open_cell(point, offset):
                    grid._stable_screenshot()
                    disc, crop = read_disc(scanner, frame(), point, page)
                if crop is not None:
                    # Keep the panel, so a slot that gave no usable reading can be looked at without another pass.
                    image = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                    if not cv2.imwrite(str(images/f'{rank}.png'), image):
                        raise OSError('驱动盘详情图保存失败')
                    save(images/f'{rank}.json', dict(rank=rank, parsed=disc))
                if usable(disc):
                    rows[rank] = disc
                    holes.discard(rank)
                    unread.discard(rank)
                elif placed(disc):
                    # Every stat is named, so the disc can be matched against the network inventory by its lines;
                    # only a value was read with too little confidence to check against the game's legal values.
                    # File it with that value left unverified: later checks against the physical disc reject it.
                    rows[rank] = disc
                    holes.discard(rank)
                    unread.add(rank)
                else:
                    # Nothing usable was read from this slot. It stays a hole for the next pass, and is reported
                    # even when no panel could be saved (the card's selection was not confirmed).
                    unread.add(rank)
            persist(page)
            if start + 36 >= total or not holes:
                frame()
                return finish_scan(root, run, total, rows)
            # Same page turn as warehouse_observations: scroll three rows.
            grid.ctx.controller.scroll(3, grid._scroll_list_center())
            time.sleep(.55)
            page += 1
    raise ValueError('仓库末尾没有对上总数')
