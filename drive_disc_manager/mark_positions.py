"""Use positions from this scan to find the discs selected for marking."""
from collections import defaultdict
from typing import NamedTuple

from .stat_reading import checked_values
from .store import fingerprint


class Position(NamedTuple):
    page: int
    column: int
    center_y: int
    stamp: str | None
    thumb: tuple | None


def scan_positions(readings, rows):
    positions = defaultdict(set)
    first_page = None
    for disc in readings:
        cell = disc.get('scanCell')
        if not isinstance(cell, (tuple, list)) or len(cell) < 2 or type(cell[0]) is not int or cell[0] < 0:
            continue
        first_page = cell[0] if first_page is None else min(first_page, cell[0])
        if len(cell) not in (3, 5):
            continue
        page, col, y = cell[:3]
        if type(col) is not int or col not in range(9) or type(y) is not int or not 180 <= y <= 955:
            continue
        stamp = thumb = None
        if len(cell) == 5:
            stamp, thumb = cell[3:]
            if (not isinstance(stamp, str) or len(stamp) != 64 or not isinstance(thumb, (tuple, list))
                    or len(thumb) != 4 or not all(type(n) is int for n in thumb)):
                continue
            thumb = tuple(thumb)
        fp = fingerprint(disc)
        row = rows.get(fp)
        if (not row or row.get('ambiguous') or not row.get('observedValues')
                or disc.get('observedGrade') != disc['rarity'] or checked_values(disc, 1) != row['observedValues']):
            continue
        positions[fp].add(Position(page, col, y, stamp, thumb))
    return {fp: sorted((p._replace(page=p.page-first_page) for p in spots), key=lambda p:(p.page, p.column, p.center_y))
            for fp, spots in positions.items()}


def page_grid_point(grid, position):
    # The native grid clicks the colored strip about 44px below the center.
    x = 167 + 135 * position.column
    matches = [(r, c, pt) for r, line in enumerate(grid) for c, pt in enumerate(line)
               if abs(pt.x-x) <= 20 and abs(pt.y-position.center_y-44) <= 20]
    return matches[0] if len(matches) == 1 else None
