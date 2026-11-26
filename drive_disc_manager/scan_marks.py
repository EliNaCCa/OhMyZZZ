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
def capture_marks(scanner, root=None):
    from threading import local
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
        from .scan_copies import scroll_thumb, selected_position, viewport_stamp
        selected = selected_position(image, grid.grid_rows[row][col])
        pending.cell = None
        if selected:
            column, y = selected
            pending.cell = (grid.scroll_rounds, column)
            thumb = scroll_thumb(image)
            stamp = viewport_stamp(image, column, thumb) if thumb else None
            if stamp:
                pending.cell += (y, stamp, thumb)
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
        yield text_cache
    finally:
        grid._save_screenshot = save_image
        worker.submit = submit
        cache.save_unique = unique
        worker.ctx = worker_ctx
