"""Recover co-visible copies conservatively from the scanner's own OCR readings."""
from collections import Counter, defaultdict

from .stat_reading import checked_values
from .store import digest, fingerprint, roll_base


def selected_position(image, target):
    """Locate the center of the game's selection border in the requested column."""
    import cv2
    import numpy as np

    if image.shape[:2] != (1080, 1920):
        return None
    x, y = round(target.x), round(target.y)
    col = round((x - 167) / 135)
    if col not in range(9) or abs(x - (167 + 135 * col)) > 20 or not 220 <= y <= 930:
        return None
    # inventory_scan fits its click points to the colored strip below the
    # disc artwork. The selection frame is centered about 44 px above them.
    # Include the whole frame, then require the click to be inside it.
    left, top = x - 90, y - 145
    crop = image[top:y+91, left:x+91]
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array([20, 120, 110]), np.array([43, 255, 255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        dx, dy, w, h = cv2.boundingRect(contour)
        if not (125 <= w <= 138 and 123 <= h <= 138 and abs(dx+w/2-90) <= 16
                and top + dy + 8 <= y <= top + dy + h - 8):
            continue
        border = mask[dy:dy+h, dx:dx+w] > 0
        if (border[:9, 18:-18].any(axis=0).mean() > .80 and
                border[18:-18, :9].any(axis=1).mean() > .80 and
                border[18:-18, -9:].any(axis=1).mean() > .80):
            return col, top + dy + h // 2
    return None


def scroll_thumb(image):
    """Return the warehouse scrollbar thumb in the fixed 1080p scrollbar band."""
    import cv2

    # Exclude the fixed arrows at the ends of the warehouse scrollbar.
    band = cv2.cvtColor(image[233:866,1355:1375], cv2.COLOR_RGB2GRAY)
    mask = cv2.inRange(band, 70, 240)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    thumbs = [cv2.boundingRect(c) for c in contours]
    thumbs = [(x,y,w,h) for x,y,w,h in thumbs if 3 <= w <= 7 and 6 <= h and 0 < y < y+h < band.shape[0]]
    return thumbs[0] if len(thumbs) == 1 else None


def viewport_stamp(image, selected_col):
    """Match scroll position and neighboring cards when comparing rows in one column."""
    import cv2
    import numpy as np
    from hashlib import sha256

    thumb = scroll_thumb(image)
    if thumb is None:
        return None
    evidence = sha256(str(thumb).encode('ascii'))
    for col in ((selected_col+3)%9, (selected_col+6)%9):
        x = 167+135*col
        strip = cv2.cvtColor(image[240:860,x-22:x+22], cv2.COLOR_RGB2GRAY)
        grid = cv2.resize(strip, (12,64), interpolation=cv2.INTER_AREA)
        if grid.std() < 24:
            return None
        # Relative brightness keeps the card pattern stable across minor variations in lighting.
        evidence.update(np.packbits(grid > np.median(grid)).tobytes())
    return evidence.hexdigest()


def page_copies(readings):
    """Independent columns are disjoint. Within a column count well-separated verified rows."""
    by_col = defaultdict(list)
    for d in readings:
        by_col[d['scanCell'][1]].append(d)
    result = []
    for observations in by_col.values():
        best = [observations[0]]
        groups = defaultdict(list)
        for disc in observations:
            cell = disc['scanCell']
            if (len(cell) == 4 and type(cell[2]) is int and 180 <= cell[2] <= 955 and
                    isinstance(cell[3], str) and len(cell[3]) == 64):
                groups[cell[3]].append((cell[2], disc))
        for positions in groups.values():
            picks, last_y = [], -1000
            for y, disc in sorted(positions, key=lambda item:item[0]):
                if y-last_y >= 150:
                    picks.append(disc)
                    last_y = y
            if len(picks) > len(best):
                best = picks
        result.extend(best)
    return result


def scanner_key(disc):
    """inventory_scan 1.4.7's export key omits grade and numeric stat values."""
    return (disc['setKey'], disc['slotKey'], disc['level'], disc['mainStatKey'],
            tuple(sorted((s['key'], s['upgrades']) for s in disc['substats'])))


def observed_siblings(exported, cells):
    """Allow a second grade only when its independent readings corroborate the cap."""
    def identity(disc):
        return fingerprint(disc), digest(disc.get('observedValues'))

    original = identity(exported)
    if original not in {identity(d) for d in cells}:
        return False
    if all(identity(d) == original for d in cells):
        return True

    grade_readings = {}
    for disc in cells:
        grade = disc.get('observedGrade')
        if grade not in ('S','A','B') or grade != disc['rarity']:
            return False
        current = identity(disc)
        if grade_readings.setdefault(grade, current) != current:
            return False
    # At the same level and slot, different grades must also have distinct main values.
    main_values = {d['observedValues']['main'][d['mainStatKey']] for d in cells}
    return len(main_values) == len(grade_readings)


def with_copies(export, readings):
    """Use the largest proven multiplicity on one page. Pages may overlap vertically."""
    audit = export.get('audit') or {}
    discs = export.get('discs', [])
    total = audit.get('game_total')
    if (any(type(audit.get(k)) is not int for k in ('game_total', 'exported', 'delta'))
            or total <= 0 or audit.get('partial') or audit.get('exported') != len(discs)
            or audit.get('delta') != len(discs)-total):
        return export  # The normal completeness check will reject an invalid export.
    groups = defaultdict(lambda: defaultdict(list))
    base = roll_base(export)
    for disc in readings:
        cell = disc.get('scanCell')
        if (not isinstance(cell, (list, tuple)) or len(cell) not in (2,4) or any(type(i) is not int for i in cell[:2])
                or cell[0] < 0 or cell[1] not in range(9) or not checked_values(disc, base)):
            continue
        if disc.get('markState') not in ('marked', 'unmarked') or disc.get('lockState') not in ('locked', 'unlocked'):
            continue
        groups[scanner_key(disc)][cell[0]].append(disc)

    # Export IDs are temporary. A fingerprint may identify a group, never an individual copy.
    counts = Counter(scanner_key(d) for d in discs)
    rebuilt, added = [], 0
    for disc in discs:
        key = scanner_key(disc)
        pages = (page_copies(rows) for rows in groups.get(key, {}).values())
        cells = max((page for page in pages if len(page) > 1 and observed_siblings(disc, page)), key=len, default=[])
        if cells and counts[key] == 1:
            rebuilt.extend(cells)
            added += len(cells)-1
        else:
            rebuilt.append(disc)
    if not added:
        return export
    if len(rebuilt) != total:
        raise ValueError(f'同页选中框已核实 {added} 张重复盘，仍无法对账：游戏内 {total}，已核实 {len(rebuilt)} 张')
    return dict(export, discs=rebuilt, audit=dict(audit, exported=len(rebuilt), delta=0,
                scannerExported=len(discs), samePageCopies=added))
