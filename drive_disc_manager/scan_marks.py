"""Bind the footer from each stable warehouse screenshot to its own OCR job."""
import re
from contextlib import contextmanager


def observed_header(items):
    # OCR may split a level label and its fraction into unordered boxes on one line.
    rows, detached = [], []
    for item in sorted(items, key=lambda i:(i.get('position') or (0, 0))[1]):
        pos = item.get('position')
        token = item.get('text', '') if item.get('confidence', 0) >= .85 else '|'
        token = ''.join(token.split()).replace('【','[').replace('】',']')
        if not pos:
            detached.append(token)
        else:
            if not rows or pos[1] - rows[-1][0] > 12:
                rows.append((pos[1], []))
            rows[-1][1].append((pos[0], token))
    lines = [''.join(t for _, t in sorted(boxes)) for _, boxes in rows] + detached
    text = '|'.join(lines)
    slots = set(re.findall(re.escape('[') + '([1-6])' + re.escape(']'), text))
    pairs = set(re.findall(r'(?:等级|Lv[.]?)([0-9]{1,2})/([0-9]+)', text, re.I))
    slot = slots.pop() if len(slots) == 1 else None
    if len(pairs) == 1:
        level, cap = pairs.pop()
        if cap in ('9', '12', '15') and int(level) <= int(cap):
            return slot, int(level), {'9':'B','12':'A','15':'S'}[cap]
    return slot, None, None


def observed_grade(items):
    return observed_header(items)[2]


class MarkReader:
    def __init__(self, parser, ocr, footer, cell=None, text_cache=None, controls=None):
        self.parser = parser
        self.ocr = ocr
        self.footer = footer
        self.cell = cell
        self.text_cache = text_cache
        self.controls = controls

    def parse_ocr_result(self, items, image):
        # The host's spelling dictionary mutates its input. Preserve raw value tokens.
        parsed_items = [dict(i, text=i['text'].replace('【','[').replace('】',']')) for i in items]
        result = self.parser.parse_ocr_result(parsed_items, None)
        if result is None:
            return None
        from .stat_reading import main_stat_label, valid_slot_main
        # The scanner's parser names a main stat by substring and falls back to HP% when nothing matches,
        # so a stat it has no entry for (wind damage on slot 5) is reported as HP%. It then drops any substat
        # equal to that main stat. Trust the game's own main-stat line when it clearly names a stat that fits
        # the slot, and read the substats again under the corrected name. The value check below still applies.
        shown = main_stat_label(items, self.parser)
        if shown and shown != result['mainStatKey'] and valid_slot_main(dict(result, mainStatKey=shown)):
            result['mainStatKey'] = shown
            # The parser sorts OCR items into reading order before its first pass but not inside _parse_substats,
            # and OCR can list both labels of two same-named lines before their values. Sort the same way first.
            result['substats'] = self.parser._parse_substats(self.parser._sort_ocr_items(parsed_items), shown)
        from .disc_owner import read_owner
        result['ownerState'], result['location'] = read_owner(image, getattr(self.parser, 'icon_matcher', None))
        mark, lock = read_controls(self.ocr, self.footer, self.controls)
        result.update(markState=mark, trash=mark == 'marked', lockState=lock, lock=lock == 'locked')
        slot, level, grade = observed_header(items)
        result['observedGrade'] = grade
        if grade:
            result.update(rarity=grade, level=level)
        from .stat_reading import measure
        result['observedValues'] = measure(items, result, self.parser) if grade and slot == str(result['slotKey']) else None
        if self.text_cache and result['observedValues']:
            from .stat_reading import checked_values
            if checked_values(result, 1):
                self.text_cache.remember(image, items)
        if self.cell is not None:
            result['scanCell'] = self.cell
        return result


def reread(reader, ocr, crop, disc, scale=2):
    """Read a panel again at twice the size when a value came back with too little confidence.

    The game draws some digits (a thin "19") small enough that the model is unsure of them at the panel's
    native size and certain at twice the size. Returns the new reading only when it names exactly the same
    set, slot, level, grade, main stat and substat lines as the first one and passes the same value check;
    otherwise None, and the caller keeps what it had.
    """
    import cv2
    big = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    items = []
    for text, boxes in (ocr.run_ocr(big) or {}).items():
        for b in boxes:
            items.append(dict(text=text, confidence=b.confidence,
                              position=(b.x/scale, b.y/scale, (b.x+b.w)/scale, (b.y+b.h)/scale)))
    if not items:
        return None
    again = reader.parse_ocr_result(items, crop)
    if not again or not again.get('observedValues') or not again.get('observedGrade'):
        return None
    if any(again.get(k) != disc.get(k) for k in ('setKey', 'slotKey', 'level', 'rarity', 'mainStatKey', 'substats')):
        return None
    return again


def read_controls(ocr, footer, recent=None):
    # These are actions offered by the game: an Unlock button means the disc IS locked.
    if recent is not None:
        from hashlib import sha256
        key = (footer.shape, footer.dtype.str, sha256(footer.tobytes()).digest())
        if key in recent:
            return recent[key]
    matches = ocr.run_ocr(footer) or {}
    tokens = {text.replace(' ', '') for text, boxes in matches.items() if any(box.confidence >= .9 for box in boxes)}
    # A detached prefix can reverse either button. Never interpret the remaining suffix alone.
    if any(text.strip() in ('解','解除','取消') for text in matches):
        return 'unknown', 'unknown'
    def control(mapping):
        values = {mapping[token] for token in tokens if token in mapping}
        return values.pop() if len(values) == 1 else 'unknown'
    result = (control({'解除弃置':'marked', '弃置':'unmarked'}),
              control({'解除锁定':'locked', '解锁':'locked', '锁定':'unlocked'}))
    if recent is not None and 'unknown' not in result:
        if len(recent) >= 32:
            recent.pop(next(iter(recent)))
        recent[key] = result
    return result


def mark_hint(ocr, footer):
    return read_controls(ocr, footer)[0]


@contextmanager
def capture_marks(scanner, root=None, guard=None):
    from threading import local
    from .scan_cell import confirmed_frame, park_off_grid
    grid = scanner.drive_disk_scanner
    worker = scanner.ocr_worker
    save_image, submit = grid._save_screenshot, worker.submit
    cache = scanner.screenshot_cache
    unique = cache.save_unique
    witnessed = set()
    controls = {}
    pending = local()
    text_cache = None
    worker_ctx = worker.ctx
    if root is not None:
        from .scan_resume import ScanTextCache
        text_cache = ScanTextCache(root, worker_ctx.ocr)

        class WorkerContext:
            ocr = text_cache

            def __getattr__(self, name):
                return getattr(worker_ctx, name)

        worker.ctx = WorkerContext()

    def capture(row, col, image):
        from .scan_copies import scroll_thumb, viewport_stamp
        image, selected = confirmed_frame(scanner, grid, row, col, image, guard)
        pending.cell = None
        if selected:
            column, y = selected
            pending.cell = (grid.scroll_rounds, column, y)
            thumb = scroll_thumb(image)
            stamp = viewport_stamp(image, column, thumb) if thumb else None
            if stamp:
                pending.cell += (stamp, thumb)
        pending.footer = image[985:1070,1050:1920].copy()
        try:
            return save_image(row, col, image)
        finally:
            pending.footer = None
            pending.cell = None

    def sample(kind, image):
        idx, duplicate = unique(kind, image)
        cell = getattr(pending, 'cell', None)
        if kind == 'drive_disk' and cell is not None:
            key = (idx, cell)
            if duplicate and key not in witnessed:
                # Same detail pixels from a selected grid cell. Read its own footer.
                enqueue('disc', image, grid.parser)
            witnessed.add(key)
        return idx, duplicate

    def enqueue(kind, image, parser):
        footer = getattr(pending, 'footer', None)
        if kind == 'disc' and footer is not None:
            if text_cache is not None:
                image = text_cache.frame(image)
            parser = MarkReader(parser, scanner.ctx.ocr, footer, pending.cell, text_cache, controls)
        return submit(kind, image, parser)

    grid._save_screenshot = capture
    worker.submit = enqueue
    cache.save_unique = sample
    try:
        with park_off_grid(grid):
            yield text_cache
    finally:
        grid._save_screenshot = save_image
        worker.submit = submit
        cache.save_unique = unique
        worker.ctx = worker_ctx
