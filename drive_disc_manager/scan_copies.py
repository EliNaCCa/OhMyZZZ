"""Recover co-visible copies conservatively from the scanner's own OCR readings."""
from collections import Counter, defaultdict

from .stat_reading import checked_values
from .store import digest, fingerprint, roll_base


def selected_column(image, target):
    """Return the physical column only when the yellow selection border surrounds the target."""
    import cv2
    import numpy as np

    if image.shape[:2] != (1080, 1920):
        return None
    x, y = round(target.x), round(target.y)
    col = round((x - 167) / 135)
    if col not in range(9) or abs(x - (167 + 135 * col)) > 20 or not 220 <= y <= 930:
        return None
    left, top = x - 90, y - 90
    crop = image[top:y+91, left:x+91]
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([20, 120, 110]), np.array([43, 255, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        dx, dy, w, h = cv2.boundingRect(contour)
        if not (125 <= w <= 138 and 123 <= h <= 138 and abs(dx+w/2-90) <= 16 and abs(dy+h/2-90) <= 24):
            continue
        border = mask[dy:dy+h, dx:dx+w] > 0
        if (border[:9, 18:-18].any(axis=0).mean() > .80 and
                border[18:-18, :9].any(axis=1).mean() > .80 and
                border[18:-18, -9:].any(axis=1).mean() > .80):
            return col
    return None


def with_copies(export, readings):
    """Use the largest proven multiplicity on one page. Pages may overlap vertically."""
    audit = export.get('audit') or {}
    discs = export.get('discs', [])
    total = audit.get('game_total')
    if (any(type(audit.get(k)) is not int for k in ('game_total', 'exported', 'delta'))
            or total <= 0 or audit.get('partial') or audit.get('exported') != len(discs)
            or audit.get('delta') != len(discs)-total):
        return export  # The normal completeness check will reject an invalid export.
    groups = defaultdict(lambda: defaultdict(dict))
    base = roll_base(export)
    for disc in readings:
        cell = disc.get('scanCell')
        if (not isinstance(cell, (list, tuple)) or len(cell) != 2 or any(type(i) is not int for i in cell)
                or cell[0] < 0 or cell[1] not in range(9) or not checked_values(disc, base)):
            continue
        if disc.get('markState') not in ('marked', 'unmarked') or disc.get('lockState') not in ('locked', 'unlocked'):
            continue
        page, col = cell
        key = fingerprint(disc), digest(disc['observedValues'])
        groups[key][page].setdefault(col, disc)

    # Export IDs are temporary. A fingerprint may identify a group, never an individual copy.
    counts = Counter(fingerprint(d) for d in discs)
    rebuilt, added = [], 0
    for disc in discs:
        fp = fingerprint(disc)
        by_page = groups.get((fp, digest(disc.get('observedValues'))), {})
        cells = max(by_page.values(), key=len, default={})
        if len(cells) > 1 and counts[fp] == 1:
            rebuilt.extend(cells.values())
            added += len(cells)-1
        else:
            rebuilt.append(disc)
    if not added:
        return export
    if len(rebuilt) != total:
        raise ValueError(f'同页选中框已核实 {added} 张重复盘，仍无法对账：游戏内 {total}，已核实 {len(rebuilt)} 张')
    return dict(export, discs=rebuilt, audit=dict(audit, exported=len(rebuilt), delta=0,
                scannerExported=len(discs), samePageCopies=added))
