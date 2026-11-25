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
    def __init__(self, parser, ocr, footer):
        self.parser = parser
        self.ocr = ocr
        self.footer = footer

    def parse_ocr_result(self, items, image):
        result = self.parser.parse_ocr_result(items, image)
        if result is None:
            return None
        state = mark_hint(self.ocr, self.footer)
        result['markState'] = state
        result['trash'] = state == 'marked'
        result['observedGrade'] = observed_grade(items)
        if result['observedGrade']:
            result['rarity'] = result['observedGrade']
        return result


def mark_hint(ocr, footer):
    text = ''.join(ocr.run_ocr(footer) or {}).replace(' ', '')
    if '解除弃置' in text:
        return 'marked'
    if '弃置' in text:
        return 'unmarked'
    return 'unknown'


@contextmanager
def capture_marks(scanner):
    from threading import local
    grid = scanner.drive_disk_scanner
    worker = scanner.ocr_worker
    save_image, submit = grid._save_screenshot, worker.submit
    pending = local()

    def capture(row, col, image):
        pending.footer = image[995:1065,1550:1920].copy()
        try:
            return save_image(row, col, image)
        finally:
            pending.footer = None

    def enqueue(kind, image, parser):
        footer = getattr(pending, 'footer', None)
        if kind == 'disc' and footer is not None:
            parser = MarkReader(parser, scanner.ctx.ocr, footer)
        return submit(kind, image, parser)

    grid._save_screenshot = capture
    worker.submit = enqueue
    try:
        yield
    finally:
        grid._save_screenshot = save_image
        worker.submit = submit
