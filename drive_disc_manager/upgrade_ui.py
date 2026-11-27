"""OneDragon adapter for the warehouse's 查看 → 驱动升级 flow."""
import re
from time import monotonic, sleep
from unicodedata import normalize

from .disc_probe import read_selected
from .warehouse import warehouse_header


def tokens(ocr, frame):
    return [(normalize('NFKC', ''.join(text.split())), box.center.x, box.center.y)
            for text, boxes in (ocr.run_ocr(frame) or {}).items() for box in boxes if box.confidence >= .88]


def one(words, labels):
    hits = [(x, y) for word, x, y in words if word in labels]
    return hits[0] if len(hits) == 1 else None


def stage(words):
    add = one(words, {'阶段添加'})
    confirm = one(words, {'升级'})
    title = one(words, {'驱动升级'}) or (any(re.fullmatch(r'.+\[[1-6]\]', t) for t, _, _ in words)
                                         and one(words, {'主属性'}) and one(words, {'副属性'}))
    if not title or not add or not confirm or confirm[0] <= add[0] or abs(confirm[1] - add[1]) > 35:
        return None
    return add, confirm


def back_button(words, shot, title=None):
    """Identify the material picker's close button or the disc detail's back button."""
    if title and not one(words, {title}):
        return None
    if stage(words):
        x, y = 1657, 210
        dark_cross = all(max(shot[y+dy, x+dx]) < 65 for dx, dy in ((0, 0), (6, 6), (-6, 6)))
        orange = all(int(shot[y+dy, x+dx, 0]) > int(shot[y+dy, x+dx, 1]) + 75
                     for dx, dy in ((8, 0), (0, -8), (-8, 0)))
        if not dark_cross or not orange:
            raise ValueError('没找到强化窗口右上角的关闭按钮')
        return x, y
    if (one(words, {'套装效果'}) and one(words, {'副属性'}) and
            any(re.fullmatch(r'.+\[[1-6]\]', text) and x < 800 and y < 190 for text, x, y in words)):
        return 112, 50
    return None


def materials(words):
    buttons = stage(words)
    if not buttons:
        return None
    rows = [(int(m[1]), int(m[2]), x, y) for t, x, y in words
            if (m := re.fullmatch(r'([0-9]+)/([0-9]+)', t)) and y < buttons[0][1]]
    for _, _, _, y in sorted(rows, key=lambda item: -item[3]):
        line = [r for r in rows if abs(r[3]-y) <= 15]
        if len(line) == 3 and all(n <= held for n, held, _, _ in line):
            return line
    return None


def preview_levels(words):
    buttons = stage(words)
    line = materials(words)
    if not buttons or not line or not any(n for n, _, _, _ in line):
        return None
    # The experience fraction sits between the two levels. Material counts also
    # use fractions, but are in a separate row below the level bar.
    pairs = []
    fractions = [(x, y) for word, x, y in words if re.fullmatch(r'[0-9]+/[1-9][0-9]*', word)]
    for x, y in fractions:
        levels = [(int(t), px) for t, px, py in words if re.fullmatch(r'(?:[0-9]|1[0-5])', t) and abs(py-y) <= 18]
        left = [n for n, px in levels if px < x]
        right = [n for n, px in levels if px > x]
        if len(left) == len(right) == 1 and y + 30 < min(item[3] for item in line):
            pairs.append((left[0], right[0]))
    return pairs[0] if len(pairs) == 1 else None


def stage_reading(ocr, shot):
    words = tokens(ocr, shot)
    pair = preview_levels(words)
    buttons, line = stage(words), materials(words)
    if pair or not buttons or not line:
        return pair, buttons
    # Full-frame detection sometimes misses a single white 0. Re-detect the XP
    # strip at its own scale, anchored to the fraction and material row.
    width = buttons[1][0] - buttons[0][0]
    fractions = [(x,y) for t,x,y in words if re.fullmatch(r'[0-9]+/[1-9][0-9]*', t)
                 and buttons[0][0]-width < x < buttons[1][0] and y+30 < min(v[3] for v in line)]
    if len(fractions) == 1:
        x,y = fractions[0]
        reach = round(width * 1.65)
        left, top = max(0, x-reach), max(0, y-35)
        strip = tokens(ocr, shot[top:y+36, left:x+reach])
        words = [w for w in words if abs(w[2]-y) > 18] + [(t,left+px,top+py) for t,px,py in strip]
    return preview_levels(words), buttons


def picker_values(ocr, shot, parser):
    """The material picker shows one main stat and a 2×2 grid of substats."""
    from .stat_reading import compact, label_maps, read_row
    main_labels, sub_labels = label_maps(parser)
    words = tokens(ocr, shot[320:515, 870:1700])

    def cell(left, top, labels, *, main=False):
        entries = sorted((x, compact(t)) for t, x, y in words if left <= x < left+410 and top <= y < top+44)
        if [t for _, t in entries] == ['empty']:
            return {}
        # The yellow preview increment follows the white current main stat.
        # OCR may return the two values as one token or separately.
        if main and entries:
            if len(entries) == 3 and re.fullmatch(r'[+][0-9.]+%?', entries[-1][1]):
                entries.pop()
            x, value = entries[-1]
            entries[-1] = x, re.sub(r'[+][0-9.]+%?$', '', value)
        parsed = read_row([(0, x, x, t, 1) for x, t in entries], labels)
        if parsed is None:
            return None
        key, value, _ = parsed
        return {key:float(value)}

    main = cell(0, 4, main_labels, main=True)
    subs = {}
    for top in (94, 144):
        for left in (0, 420):
            stat = cell(left, top, sub_labels)
            if stat is None or subs.keys() & stat.keys():
                return None
            subs.update(stat)
    return dict(main=main, subs=subs) if main else None


class UpgradeUI:
    def __init__(self, scanner, guard, total, names):
        self.scanner = scanner
        self.guard = guard
        self.total = total
        self.names = names
        self.ocr = scanner.ctx.ocr
        self.controller = scanner.ctx.controller
        self.expected_set = None
        self.title = None

    def frame(self):
        self.guard()
        shot = self.scanner.screenshot()
        if shot is None or shot.shape[:2] != (1080, 1920):
            raise ValueError('没有读到游戏画面')
        return shot

    def wait(self, read, message):
        end = monotonic() + 6
        while monotonic() < end:
            value = read(tokens(self.ocr, self.frame()))
            if value is not None:
                return value
            sleep(.2)
        raise ValueError(message)

    def click(self, xy):
        from one_dragon.base.geometry.point import Point
        self.guard()
        if not self.controller.click(Point(*xy)):
            raise ValueError('驱动盘按钮未响应')
        sleep(.5)

    def is_detail(self, words):
        return (any(self.expected_set in t for t, _, _ in words) and one(words, {'主属性'}) and one(words, {'副属性'})
                and not stage(words))

    def prepare(self, before, target):
        shot = self.frame()
        if warehouse_header(self.ocr, shot) != ('驱动仓库', self.total):
            raise ValueError('已离开驱动仓库')
        button = one(tokens(self.ocr, shot[800:870, 1580:1840]), {'查看'})
        if button is None:
            raise ValueError('没找到选中驱动盘的「查看」')
        self.expected_set = self.names.get(before['setKey'], before['setKey'])
        self.expected_values = before['observedValues']
        self.title = self.expected_set + '[' + str(before['slotKey']) + ']'
        self.click((1580 + button[0], 800 + button[1]))
        shot = self.frame()
        words = tokens(self.ocr, shot)
        if not one(words, {self.title}) or not self.is_detail(words):
            raise ValueError('没读到这张盘的详情')
        # Current CN client: the green double chevron next to the level opens the material picker.
        roi = shot[207:244,1391:1455].astype('int16')
        if ((roi[:,:,1]>100) & (roi[:,:,1]>roi[:,:,2]+40)).sum() < 55:
            raise ValueError('没找到等级旁的升级箭头')
        self.click((1421,226))
        def empty_stage(words):
            buttons = stage(words)
            if not buttons or not one(words, {self.title}):
                return None
            counts = materials(words)
            # With no XP materials selected, the game may hide the target level.
            if not counts or any(n for n, _, _, _ in counts):
                return None
            return buttons[0]
        self.click(self.wait(empty_stage, '没读清本次选择的镀剂'))

    def preview(self):
        end = monotonic() + 6
        while monotonic() < end:
            shot = self.frame()
            pair, _ = stage_reading(self.ocr, shot)
            if pair:
                self.check_piece(shot)
                return pair
            sleep(.2)
        raise ValueError('没读清升级前后的等级')

    def check_piece(self, shot):
        if not one(tokens(self.ocr, shot[155:270,215:1020]), {self.title}):
            raise ValueError('强化窗口的套装或号位与选中盘不符')
        if picker_values(self.ocr, shot, self.scanner.drive_disk_scanner.parser) != self.expected_values:
            raise ValueError('强化窗口的主副属性与选中盘未对上')

    def confirm(self, current, goal, before_click):
        shot = self.frame()
        pair, buttons = stage_reading(self.ocr, shot)
        if pair != (current, goal):
            raise ValueError('强化预览已变化')
        self.check_piece(shot)
        self.guard()
        before_click()
        self.click(buttons[1])

    def read_after(self):
        # Only leave a recognized upgrade/detail screen, never accept a second spend dialog.
        end = monotonic() + 14
        left = set()
        while monotonic() < end:
            shot = self.frame()
            if warehouse_header(self.ocr, shot) == ('驱动仓库', self.total):
                return read_selected(self.scanner, self.total, self.guard)
            words = tokens(self.ocr, shot)
            if any('升级成功' in t or '强化成功' in t for t, _, _ in words):
                if 'success' not in left:
                    left.add('success')
                    self.guard()
                    self.controller.keyboard_controller.tap('esc')
                sleep(.25)
            else:
                back = back_button(words, shot, self.title)
                if back and back not in left:
                    left.add(back)
                    self.click(back)
                else:
                    sleep(.25)
        raise ValueError('强化已提交，等下次整理核对等级')
