"""Capture each newly exposed warehouse slot once, keeping its detail image for offline OCR."""
from pathlib import Path
import time

import cv2
import numpy as np

from .store import read, save


def row_images(frame, centers):
    return [np.stack([cv2.resize(frame[y-41:y+32, x-36:x+37], (18, 18),
                                interpolation=cv2.INTER_AREA) for x in range(167, 1300, 135)]) for y in centers]


def displacement(previous, current):
    """The overlapping artwork must agree across at least seven columns of each shared row."""
    matches = []
    for shift in range(len(previous)):
        overlap = min(len(previous)-shift, len(current))
        for old, new in zip(previous[shift:shift+overlap], current[:overlap]):
            diff = np.abs(old.astype('int16') - new).mean(axis=(1, 2, 3))
            if (diff < 6).sum() < 7:
                break
        else:
            matches.append(shift)
    if len(matches) != 1:
        raise ValueError('翻页重叠的盘位没对上')
    return matches[0]


def capture(scanner, total, guard, run, root, seed):
    from one_dragon.base.geometry.point import Point
    from .disc_order import finish_scan
    from .manual_equip import guarded_grid
    from .scan_cell import selected_card
    from .scan_copies import scroll_thumb
    from .warehouse import warehouse_header
    from .warehouse_sort import newest_first
    from .scan_marks import MarkReader
    from .scan_resume import ScanTextCache

    if not newest_first(scanner, total, guard):
        raise ValueError('获得顺序的方向未核实')
    ocr = scanner.ctx.ocr
    cache = ScanTextCache(root, ocr)
    controls = {}
    rect = scanner.ctx.screen_loader.get_area('仓库-驱动仓库', '驱动盘属性').rect
    images = run/'panels'
    images.mkdir(exist_ok=True)
    rows = dict(seed)
    captured = set()
    archive = run/'progress.json'
    write_status = lambda page: save(run/'status.json', dict(status='reading', total=total, page=page,
        ordered=len(rows), captured=len(captured | rows.keys()), missingStats=sorted(captured-rows.keys())))

    def frame():
        guard()
        screen = scanner.screenshot()
        if screen is None or screen.shape[:2] != (1080, 1920):
            raise ValueError('游戏画面尺寸变化')
        return screen

    def verified_page():
        screen = frame()
        if warehouse_header(ocr, screen) != ('驱动仓库', total):
            raise ValueError('仓库画面或盘数已变化')
        return screen

    def persist(page):
        save(archive, dict(account=root.name, total=total, rows=rows, captured=sorted(captured)))
        write_status(page)

    def detail(grid, rank, point):
        guard()
        if not grid.ctx.controller.click(point, press_time=.06):
            raise ValueError('选盘失败')
        grid.ctx.controller.mouse_move(Point(40,182))
        time.sleep(.15)
        # Accept only a stable panel with this specific card visibly selected.
        last = None
        for _ in range(12):
            screen = frame()
            chosen = selected_card(screen, point)
            if chosen and abs(chosen[1]-point.y) <= 7:
                crop = screen[rect.y1:rect.y2,rect.x1:rect.x2]
                reduced = cv2.resize(crop,(108,125),interpolation=cv2.INTER_AREA).astype('int16')
                if last is not None and np.abs(last-reduced).mean() < .55:
                    return screen,crop
                last = reduced
            time.sleep(.08)
        raise ValueError(f'第 {rank} 张盘的选中框或详情未稳定')

    previous = None
    origin = 0
    anchors = set()
    with guarded_grid(scanner.drive_disk_scanner, guard) as grid:
        center = Point(700, 540)
        for _ in range(4):
            grid.ctx.controller.scroll(-160, center)
            time.sleep(.40)
            top = scroll_thumb(verified_page())
            if total <= 36 or (top and top[1] <= 7):
                break
        else:
            raise ValueError('获得顺序未返回第一张')
        for page in range((total+8)//9+2):
            screen = verified_page()
            grid.grid_rows, _ = grid._resolve_grid(grid._detect_points(screen))
            centers = [int(line[0].y)-44 for line in grid.grid_rows if line and 248 <= int(line[0].y)-44 <= 845]
            if not 1 <= len(centers) <= 4 or any(abs(b-a-174) > 5 for a,b in zip(centers,centers[1:])):
                raise ValueError('驱动盘网格行距未核实')
            art = row_images(screen, centers)
            if previous is not None:
                shift = displacement(previous, art)
                if not shift:
                    raise ValueError('仓库翻页没有前进')
                origin += shift*9
            elif abs(centers[0]-275)>6:
                raise ValueError('第一行盘位未对齐')
            previous = art
            end = min(total, origin+9*len(centers))
            old = [i for i in range(origin+1,end+1) if i in seed and i not in anchors]
            checks = {old[0],old[-1]} if old else set()
            for rank in range(origin+1, end+1):
                if rank not in checks and (rank in rows or rank in captured):
                    continue
                row,col = divmod(rank-origin-1,9)
                point = Point(167+135*col, centers[row])
                screen,crop = detail(grid,rank,point)
                footer = screen[985:1070,1050:1920]
                panel = cache.frame(crop)
                items = [dict(text=t, confidence=b.confidence, position=(b.x,b.y,b.x+b.w,b.y+b.h))
                         for t,boxes in (cache.run_ocr(panel) or {}).items() for b in boxes]
                reader = MarkReader(grid.parser, ocr, footer, (page,col,centers[row]), cache,controls)
                disc = reader.parse_ocr_result(items, panel)
                if rank in checks:
                    from .store import fingerprint
                    if not disc or fingerprint(disc) != fingerprint(seed[rank]):
                        raise ValueError(f'第 {rank} 张盘与本轮已有读数不同')
                    anchors.add(rank)
                if disc and disc.get('observedGrade') and disc.get('observedValues'):
                    rows[rank] = disc
                else:
                    # Keep only what is needed to re-read the slot without another warehouse pass.
                    image = cv2.cvtColor(crop,cv2.COLOR_RGB2BGR)
                    if not cv2.imwrite(str(images/f'{rank}.png'),image):
                        raise OSError('驱动盘详情图保存失败')
                    save(images/f'{rank}.json',dict(rank=rank, items=items, parsed=disc))
                captured.add(rank)
                if rank % 9 == 0:
                    persist(page)
            persist(page)
            if end == total:
                verified_page()
                return finish_scan(root, run, total, rows)
            grid.ctx.controller.scroll(min(3, max(1, (total-end+8)//9)),center)
            time.sleep(.5)
    raise ValueError('仓库末尾没有对上总数')
