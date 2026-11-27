"""Count identical-looking discs from the scanner's verified grid positions."""
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


def viewport_stamp(image, selected_col, thumb=None):
    """Match scroll position and neighboring cards when comparing rows in one column."""
    import cv2
    import numpy as np
    from hashlib import sha256

    thumb = thumb or scroll_thumb(image)
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


def scroll_band(cell, total):
    """Bound the part of the scrollbar that one card could occupy as it scrolls."""
    if len(cell) != 5 or not isinstance(cell[4], (tuple, list)) or len(cell[4]) != 4:
        return None
    if not all(type(i) is int for i in cell[4]):
        return None
    x, top, width, height = cell[4]
    row_count = (total + 8) // 9
    if row_count <= 4:
        return None
    # At 1080p the track spans about 650px and four warehouse rows are visible.
    expected = 650 * 4 / row_count
    if (not 3 <= width <= 7 or not 0 <= x <= 20-width or not 0 < top < top+height < 633 or
            height < 8 or abs(height-expected) > max(2, expected * .08)):
        return None
    # Extend both ends by half a viewport plus jitter for the disjoint-page check.
    margin = (height+1)//2 + 3
    return x, width, top-margin, top+height+margin


def scroll_runs(readings, total):
    """Split the scan at conflicting viewport evidence or a change of direction."""
    pages = defaultdict(set)
    stamps = defaultdict(lambda: defaultdict(set))
    for disc in readings:
        cell = disc['scanCell']
        page, column = cell[:2]
        band = scroll_band(cell, total)
        pages[page].add(tuple(cell[4]) if band and isinstance(cell[3], str) and len(cell[3]) == 64 else None)
        if band and isinstance(cell[3], str):
            stamps[page][column].add(cell[3])

    runs = {}
    run = 0
    track = None
    first_height = furthest = 0
    for page, thumbs in sorted(pages.items()):
        if len(thumbs) != 1 or None in thumbs or any(len(seen) > 1 for seen in stamps[page].values()):
            track = None
            continue
        x, top, width, height = next(iter(thumbs))
        if track != (x, width) or abs(height - first_height) > 1 or top < furthest - 2:
            run += 1
            track, first_height, furthest = (x, width), height, top
        runs[page] = run
        furthest = max(furthest, top)
    return runs


def ordered_column(pages):
    """Longest chain of provably distinct selected cards in a single column."""
    pages = sorted(pages, key=lambda page: page[0])
    sightings = [(page, start, end, d) for page, start, end, picks in pages for d in picks]
    length, parent = [], []
    for index, (page, start, _, disc) in enumerate(sightings):
        cell = disc['scanCell']
        best, previous = 1, None
        for i in range(index):
            if length[i] + 1 <= best:
                continue
            old_page, _, old_end, old = sightings[i]
            old_cell = old['scanCell']
            # During a downward scroll the same card moves up the screen. A
            # later selection at the same height or below, with a clearly
            # advanced thumb, is another physical copy even on overlapping pages.
            if page == old_page:
                distinct = cell[3] == old_cell[3] and cell[2] - old_cell[2] >= 150
            else:
                top, height = cell[4][1], cell[4][3]
                old_top, old_height = old_cell[4][1], old_cell[4][3]
                distinct = abs(height - old_height) <= 1 and (
                    start > old_end or (top - old_top >= 8 and cell[2] >= old_cell[2] - 2))
            if distinct:
                best, previous = length[i] + 1, i
        length.append(best)
        parent.append(previous)
    if not length:
        return []
    last = max(range(len(length)), key=length.__getitem__)
    result = []
    while last is not None:
        result.append(sightings[last][3])
        last = parent[last]
    return result[::-1]


def grid_copies(readings, total, runs=None):
    """Count distinct cards using grid columns, selected rows and scroll direction."""
    by_col = defaultdict(list)
    for d in readings:
        by_col[d['scanCell'][1]].append(d)
    if runs is None:
        runs = scroll_runs((d for col in by_col.values() for d in col), total)
    result = []
    for observations in by_col.values():
        best = [observations[0]]
        groups = defaultdict(list)
        for disc in observations:
            cell = disc['scanCell']
            if (len(cell) in (4, 5) and type(cell[2]) is int and 180 <= cell[2] <= 955 and
                    isinstance(cell[3], str) and len(cell[3]) == 64):
                groups[(cell[0], cell[3])].append((cell[2], disc))
        tracks = defaultdict(list)
        for (page, _), positions in groups.items():
            picks, last_y = [], -1000
            for y, disc in sorted(positions, key=lambda item:item[0]):
                if y-last_y >= 150:
                    picks.append(disc)
                    last_y = y
            if len(picks) > len(best):
                best = picks
            if page in runs:
                _, _, start, end = scroll_band(picks[0]['scanCell'], total)
                tracks[runs[page]].append((page, start, end, picks))
        for pages in tracks.values():
            chain = ordered_column(pages)
            if len(chain) > len(best):
                best = chain
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
    """Restore the visible copies before the caller checks the warehouse total."""
    audit = export.get('audit') or {}
    discs = export.get('discs', [])
    total = audit.get('game_total')
    if (any(type(audit.get(k)) is not int for k in ('game_total', 'exported', 'delta'))
            or total <= 0 or audit.get('partial') or audit.get('exported') != len(discs)
            or audit.get('delta') != len(discs)-total):
        return export  # The normal completeness check will reject an invalid export.
    groups = defaultdict(lambda: defaultdict(list))
    positioned = []
    base = roll_base(export)
    for disc in readings:
        cell = disc.get('scanCell')
        if (not isinstance(cell, (list, tuple)) or len(cell) not in (2,3,4,5) or any(type(i) is not int for i in cell[:2])
                or cell[0] < 0 or cell[1] not in range(9)):
            continue
        positioned.append(disc)
        if not checked_values(disc, base):
            continue
        if disc.get('markState') not in ('marked', 'unmarked') or disc.get('lockState') not in ('locked', 'unlocked'):
            continue
        groups[scanner_key(disc)][cell[0]].append(disc)

    # Export IDs are temporary. A fingerprint may identify a group, never an individual copy.
    counts = Counter(scanner_key(d) for d in discs)
    runs = scroll_runs(positioned, total)
    rebuilt, same_page, across_pages = [], 0, 0
    for disc in discs:
        key = scanner_key(disc)
        pages = groups.get(key, {})
        readings_by_page = (grid_copies(rows, total, runs) for rows in pages.values())
        cells = max((page for page in readings_by_page if len(page) > 1 and observed_siblings(disc, page)), key=len, default=[])
        on_one_page = max(1, len(cells))
        if len(pages) > 1:
            # Different columns stay disjoint as the nine-column grid moves vertically.
            combined = grid_copies((d for rows in pages.values() for d in rows), total, runs)
            if len(combined) > on_one_page and observed_siblings(disc, combined):
                cells = combined
        if cells and counts[key] == 1:
            rebuilt.extend(cells)
            same_page += on_one_page - 1
            across_pages += len(cells) - on_one_page
        else:
            rebuilt.append(disc)
    if not (same_page or across_pages):
        return export
    return dict(export, discs=rebuilt, audit=dict(audit, exported=len(rebuilt), delta=len(rebuilt)-total,
                scannerExported=len(discs), samePageCopies=same_page, crossPageCopies=across_pages))
