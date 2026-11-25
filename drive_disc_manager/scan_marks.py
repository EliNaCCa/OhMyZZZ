"""Bind the footer from each stable warehouse screenshot to its own OCR job."""
import re
from contextlib import contextmanager


def observed_grade(items):
    # The host parser defaults rarity to S. Use an explicit enhancement cap instead.
    text = ''.join(str(i.get('text','')) for i in items if i.get('confidence',0) >= 0.85).replace(' ','')
    caps = re.findall(r'(?:等级|Lv[.]?)(?:[0-9]{1,2})/(9|12|15)(?![0-9])', text, re.I)
    if len(set(caps)) == 1:
        return {'9':'B','12':'A','15':'S'}[caps[0]]


class MarkReader:
    def __init__(self, parser, ocr, footer, cell=None):
        self.parser = parser
        self.ocr = ocr
        self.footer = footer
        self.cell = cell

    def parse_ocr_result(self, items, image):
        # The host's spelling dictionary mutates its input. Preserve raw value tokens.
        result = self.parser.parse_ocr_result([dict(i) for i in items], None)
        if result is None:
            return None
        from .disc_owner import read_owner
        result['ownerState'], result['location'] = read_owner(image, getattr(self.parser, 'icon_matcher', None))
        mark, lock = read_controls(self.ocr, self.footer)
        result.update(markState=mark, trash=mark == 'marked', lockState=lock, lock=lock == 'locked')
        result['observedGrade'] = observed_grade(items)
        if result['observedGrade']:
            result['rarity'] = result['observedGrade']
        from .stat_reading import measure
        result['observedValues'] = measure(items, result, self.parser) if result['observedGrade'] else None
        if self.cell is not None:
            result['scanCell'] = self.cell
        return result


def read_controls(ocr, footer):
    # These are actions offered by the game: an Unlock button means the disc IS locked.
    matches = ocr.run_ocr(footer) or {}
    tokens = {text.replace(' ', '') for text, boxes in matches.items() if any(box.confidence >= .9 for box in boxes)}
    # A detached prefix can reverse either button. Never interpret the remaining suffix alone.
    if any(text.strip() in ('解','解除','取消') for text in matches):
        return 'unknown', 'unknown'
    def control(mapping):
        values = {mapping[token] for token in tokens if token in mapping}
        return values.pop() if len(values) == 1 else 'unknown'
    return (control({'解除弃置':'marked', '弃置':'unmarked'}),
            control({'解除锁定':'locked', '解锁':'locked', '锁定':'unlocked'}))


def mark_hint(ocr, footer):
    return read_controls(ocr, footer)[0]


@contextmanager
def capture_marks(scanner):
    from threading import local
    grid = scanner.drive_disk_scanner
    worker = scanner.ocr_worker
    save_image, submit = grid._save_screenshot, worker.submit
    cache = scanner.screenshot_cache
    unique = cache.save_unique
    witnessed = set()
    pending = local()

    def capture(row, col, image):
        from .scan_copies import selected_position, viewport_stamp
        selected = selected_position(image, grid.grid_rows[row][col])
        pending.cell = None
        if selected:
            column, y = selected
            pending.cell = (grid.scroll_rounds, column)
            stamp = viewport_stamp(image, column)
            if stamp:
                pending.cell += (y, stamp)
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
            parser = MarkReader(parser, scanner.ctx.ocr, footer, pending.cell)
        return submit(kind, image, parser)

    grid._save_screenshot = capture
    worker.submit = enqueue
    cache.save_unique = sample
    try:
        yield
    finally:
        grid._save_screenshot = save_image
        worker.submit = submit
        cache.save_unique = unique
