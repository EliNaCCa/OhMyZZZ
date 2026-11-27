"""Select 获得顺序 in the game's warehouse filter, newest first."""
import logging
import re
from time import monotonic, sleep

import cv2
from one_dragon.base.geometry.point import Point

from .warehouse import warehouse_header


def words(ocr, frame, rect, *, brighten=False):
    x, y, right, bottom = rect
    crop = frame[y:bottom, x:right]
    if brighten:
        crop = cv2.normalize(crop, None, 0, 255, cv2.NORM_MINMAX)
    return {text.replace(' ', '') for text, matches in (ocr.run_ocr(crop) or {}).items()
            if any(box.confidence >= .80 for box in matches)}


def first_level(ocr, frame):
    # The panel dims the cards. Read the first card in the level-sorted grid.
    labels = words(ocr, frame, (100, 333, 238, 379), brighten=True)
    for text in labels:
        match = re.fullmatch(r'等级(1[0-5]|[0-9])', text)
        if match:
            return int(match[1])
    return None


def newest_first(scanner, total, guard):
    ocr = scanner.ctx.ocr

    def frame():
        guard()
        shot = scanner.screenshot()
        if shot is None or shot.shape[:2] != (1080, 1920):
            raise ValueError('驱动仓库需要 1920×1080 游戏画面')
        return shot

    def wait(read, message):
        end = monotonic() + 4
        while monotonic() < end:
            value = read(frame())
            if value is not None and value is not False:
                return value
            sleep(.15)
        raise ValueError(message)

    def click(x, y):
        guard()
        if not scanner.ctx.controller.click(Point(x, y)):
            raise ValueError('仓库排序按钮未响应')
        sleep(.45)

    def panel(shot):
        return any('驱动盘筛选' in t for t in words(ocr, shot, (1268, 32, 1490, 91)))

    def current(shot):
        keys = words(ocr, shot, (1310, 136, 1510, 192)) & {'稀有度', '等级', '获得顺序'}
        return next(iter(keys)) if len(keys) == 1 else None

    def select(label, y):
        if wait(current, '排序选项没读清') == label:
            return
        click(1736, 164)
        wait(lambda s: label in words(ocr, s, (1306, y-29, 1470, y+29)), '找不到排序选项：'+label)
        click(1362, y)
        wait(lambda s: current(s) == label, '切换排序失败：'+label)

    def direction():
        # The arrow button has identical pixels in both states. Level sort and
        # acquisition sort share the direction. Compare the two ends in game.
        select('等级', 308)
        def level(shot):
            return first_level(ocr, shot)

        before = wait(level, '没读清第一个盘的等级')
        click(1805, 164)
        after = wait(level, '没读清反向排序后的等级')
        if before >= after:
            click(1805, 164)
            wait(lambda s: level(s) == before, '排序方向未切换')
        return before != after

    shot = frame()
    if not panel(shot):
        if warehouse_header(ocr, shot) != ('驱动仓库', total):
            raise ValueError('仓库盘数已变化，请刷新库存')
        click(149, 930)
        wait(panel, '驱动盘筛选没有打开')
    try:
        verified = direction() if total > 1 else True
        select('获得顺序', 375)
        if not verified:
            logging.getLogger(__name__).info('首尾盘等级相同，沿用原有正反序，按本轮清单找盘')
    finally:
        if panel(frame()):
            click(1804, 50)
    wait(lambda s: not panel(s) and warehouse_header(ocr, s) == ('驱动仓库', total), '未返回驱动仓库')
    return verified
