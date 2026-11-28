"""A resume pass over a simulated warehouse. No game window is used.

The simulated list scrolls three rows per turn like the live one. Reading a card returns the disc the list shows
in the clicked cell, or a disc without stat values where a hole is unreadable. Only the one function that looks
at the game frame is replaced; the pass itself, its position bookkeeping and its stopping rules are the real ones.
"""
import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--home', required=True, type=Path)
ap.add_argument('--seed', required=True, type=Path,
                help='a progress.json of real readings: the sample every simulated warehouse is built from')
args = ap.parse_args()
home, seed_file = args.home, args.seed
repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
import numpy as np
from one_dragon.base.geometry.point import Point

_saved = json.loads(seed_file.read_text('utf-8'))
REAL = {int(k): v for k, v in _saved['rows'].items()}
REAL_TOTAL = _saved['total']


def warehouse(total, discs, unreadable, blanks=(), stuck_after=None, invisible=()):
    """A 9-column, 4-row window over `discs` ({position: reading}), moved by scroll(n rows).

    A position in `unreadable` returns its disc with the stat values missing: every line is named, but a value
    was read with too little confidence to check. A position in `invisible` returns nothing at all.
    """
    state = dict(top=0, clicks=[], turns=0)

    def shown(offset):
        rank = state['top']*9 + offset + 1
        if rank not in discs or rank in invisible:
            return None
        return dict(discs[rank], observedValues=None) if rank in unreadable else discs[rank]

    points = [[Point(167 + 135*c, 275 + 174*r) for c in range(9)] for r in range(4)]
    def ensure(point, row, col):
        offset = row*9 + col
        if offset in blanks or state['top']*9 + offset + 1 > total:
            return False
        state['clicks'].append(offset)
        state['selected'] = offset
        return True
    def scroll(amount, pos):
        state['turns'] += amount // 3 if amount > 0 else 0
        if amount > 0 and stuck_after is not None and state['turns'] > stuck_after:
            return                       # the list no longer moves
        state['top'] = max(0, state['top'] + amount)
    grid = SimpleNamespace(_detect_points=lambda frame: [p for line in points for p in line],
                           _resolve_grid=lambda pts: (points, ''), _ensure_selected=ensure,
                           _stable_screenshot=lambda: None, _scroll_list_center=lambda: Point(700, 540),
                           parser=SimpleNamespace())
    controller = SimpleNamespace(scroll=scroll, mouse_move=lambda p: None)
    from one_dragon.base.geometry.rectangle import Rect
    # capture() checks the warehouse title through ctx.ocr on every frame; that check is patched to pass below.
    ctx = SimpleNamespace(controller=controller, ocr=SimpleNamespace(run_ocr=lambda crop: {}),
                          screen_loader=SimpleNamespace(get_area=lambda *_: SimpleNamespace(rect=Rect(1408, 267, 1838, 768))),
                          run_context=SimpleNamespace(is_context_running=True))
    scanner = SimpleNamespace(ctx=ctx, drive_disk_scanner=grid, screenshot=lambda: np.zeros((1080, 1920, 3), np.uint8),
                              _ensure_screen_areas=lambda: None)
    return scanner, state, shown


def resume(total, discs, seed, unreadable=(), blanks=(), stuck_after=None, invisible=()):
    from drive_disc_manager import order_capture
    scanner, state, shown = warehouse(total, discs, unreadable, blanks, stuck_after, invisible)
    def read_disc(scanner_arg, screen, point, page):
        disc = shown(state.get('selected', -1))
        return (disc, np.zeros((501, 430, 3), np.uint8)) if disc else (None, None)
    with TemporaryDirectory() as tmp:
        run_dir, root = Path(tmp)/'run', Path(tmp)/'account'/'1'
        run_dir.mkdir()                     # the launcher creates the run folder before the pass starts
        with patch.object(order_capture, 'read_disc', read_disc), \
             patch('drive_disc_manager.order_capture.cv2.imwrite', lambda path, image: True), \
             patch('drive_disc_manager.warehouse_sort.newest_first', lambda scanner, total, guard: True), \
             patch('drive_disc_manager.warehouse.warehouse_header', lambda ocr, screen: ('驱动仓库', total)), \
             patch('drive_disc_manager.scan_copies.scroll_thumb',
                   lambda frame: (8, 5, 5, 18) if state['top'] == 0 else (8, 60, 5, 18)), \
             patch('drive_disc_manager.manual_equip.guarded_grid',
                   lambda grid, guard: _identity(grid, scanner, controller=scanner.ctx.controller)):
            result = order_capture.capture(scanner, total, lambda: None, run_dir, root, seed)
        saved = run_dir/'progress.json'
        progress = json.loads(saved.read_text('utf-8')) if saved.is_file() else {}
    return result, state, progress


@contextmanager
def _identity(grid, scanner, controller):
    grid.ctx = SimpleNamespace(controller=controller)
    yield grid


def expect_stop(label, needle, *args, **kw):
    try:
        resume(*args, **kw)
    except ValueError as exc:
        if needle not in str(exc):
            raise AssertionError(f'{label}: stopped for another reason · {exc}')
        print('PASS  ' + label + ' · ' + str(exc))
        return
    raise AssertionError(f'{label}: the pass did not stop')


def fixture(total, real):
    """A warehouse of distinct, well-formed discs built from real readings.

    Real readings are known to be discs the game can show, so the rules of the game need not be restated here.
    A template is turned into as many distinct discs as needed by cycling its set name and the main stats its
    slot allows (never one that repeats among its substats), until no two positions hold the same disc.
    """
    sets = ['SwingJazz', 'WoodpeckerElectro', 'SoulRock', 'AstralVoice', 'Branch&BladeSong', 'KingoftheSummit',
            "Dawn'sBloom", 'MoonlightLullaby', 'HormonePunk', 'FangedMetal', 'PufferElectro', 'PhaethonsMelody']
    from drive_disc_manager.subscriptions import SLOT_MAIN
    from drive_disc_manager.store import fingerprint
    templates = [d for d in real.values() if d.get('observedValues') and len(d['substats']) in (3, 4)]
    assert templates, 'the real readings contain no fully checked disc to build a warehouse from'
    out, used = {}, set()
    rank = 0
    for t in templates:
        subs = [s['key'] for s in t['substats']]
        for set_key in sets:
            for main in SLOT_MAIN[str(t['slotKey'])]:
                if main in subs:
                    continue
                candidate = dict(t, setKey=set_key, mainStatKey=main)
                mark = fingerprint(candidate)
                if mark in used:
                    continue
                used.add(mark)
                rank += 1
                out[rank] = candidate
                if rank == total:
                    return out
    raise AssertionError('the real readings could not yield %d distinct discs' % total)


def well_formed(discs):
    """Every disc has a main stat valid for its slot, and roll counts that fit its level and line count."""
    from drive_disc_manager.reserve_policy import development
    from drive_disc_manager.stat_reading import valid_slot_main
    return all(valid_slot_main(d) and development(d['level'], {s['key']: s['upgrades'] for s in d['substats']}) is not None
               and d['mainStatKey'] not in {s['key'] for s in d['substats']} for d in discs.values())


def distinct(discs):
    from drive_disc_manager.store import fingerprint
    return len({fingerprint(d) for d in discs.values()}) == len(discs)


def filed(rows, discs, total):
    """True when every position holds exactly the disc the simulated warehouse has there."""
    from drive_disc_manager.store import fingerprint
    return set(rows) == set(range(1, total + 1)) and all(fingerprint(rows[r]) == fingerprint(discs[r]) for r in range(1, total + 1))


# 1) Holes at the very top, across a page boundary, in the middle and at the very end.
total = 200
discs = fixture(total, REAL)
assert distinct(discs) and well_formed(discs), "the fixture must hold distinct, well-formed discs"
holes = {1, 9, 27, 28, 36, 37, 100, 101, 164, 199, 200}
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r not in holes})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and len(rows) == total, result
screens = state['turns'] + 1
# One disc is enough to locate a screen whose discs are all distinct: the holes plus one reading per screen.
assert len(state['clicks']) == len(holes) + screens, f'{len(state["clicks"])} clicks for {len(holes)} holes on {screens} screens'
assert filed(rows, discs, total), 'a reading was filed under the wrong position'
print(f'PASS  {len(holes)} holes read once each, {total} positions filed, {len(state["clicks"])} clicks on {screens} screens')

# 1b) Two identical discs in the top row: one reading cannot place the screen, so a second is read.
total = 120
discs = fixture(total, REAL)
for rank in (28, 32):                       # both on the top row of the second screen
    discs[rank] = dict(discs[29], setKey=discs[29]['setKey'])
    discs[rank]['substats'] = [dict(s) for s in discs[29]['substats']]
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 40})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and filed(rows, discs, total), 'misplaced with duplicate discs'
print('PASS  duplicate discs in the top row are resolved by a second reading, nothing misplaced')

# 2) A hole whose every line is named but whose values could not be checked is filed for the order record
#    with its values left empty and reported, so it can be matched against the network inventory and is still
#    refused by any later check against the physical disc.
total = 40
discs = fixture(total, REAL)
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 30}, unreadable={30})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and len(rows) == total, (result, len(rows))
assert 30 in rows and rows[30]['observedValues'] is None and progress['unread'] == [30], (progress.get('unread'), rows.get(30))
assert all(rows[r].get('observedValues') for r in rows if r != 30), 'a checked reading lost its values'
assert [s['key'] for s in rows[30]['substats']] == [s['key'] for s in discs[30]['substats']]
from drive_disc_manager.stat_reading import checked_values
assert checked_values(rows[30], 1) is None, 'an unverified reading passed the value check'
print('PASS  a hole named in full but with an unchecked value is filed for the order, its value left empty')

# 2b) A hole whose lines themselves cannot be read is never filed.
total = 40
discs = fixture(total, REAL)
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 30}, invisible={30})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'partial' and 30 not in rows and progress['unread'] == [30], (result, progress.get('unread'))
print('PASS  a hole whose lines cannot be read is reported and is not filed')

# 3) A screen where no disc can be matched: stop, do not guess. Nothing on the top row of the very first
#    screen gives a reading, so there is nothing known to place it by.
total = 120
discs = fixture(total, REAL)
expect_stop('a screen with nothing to place it by stops the pass', '没有对上已知的盘', total, discs,
            {r: d for r, d in discs.items() if r > 9}, invisible=set(range(1, 10)))

# 3b) The same on a later screen. Screens overlap by one row, and a hole read on one screen can place the next,
#     so the screen that must stop is one whose every position gives no reading at all.
total = 120
discs = fixture(total, REAL)
expect_stop('a later screen with nothing known stops the pass', '没有对上已知的盘', total, discs,
            {r: d for r, d in discs.items() if not 28 <= r <= 91}, invisible=set(range(28, 92)))

# 3c) A disc named in full but unchecked still places a screen. Screens overlap by one row: the last row of
#     one screen is the first row of the next. Positions 28-36 are filed unchecked (every line named, no value),
#     so the second screen has nothing but unchecked discs to be placed by.
total = 120
discs = fixture(total, REAL)
unchecked = {r: dict(discs[r], observedValues=None) for r in range(28, 37)}
seed = {r: d for r, d in discs.items() if r <= 9 or 37 <= r <= 63}
seed.update(unchecked)
result, state, progress = resume(total, discs, seed)
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and filed(rows, discs, total), 'a screen was not placed by unchecked discs'
assert all(rows[r]['observedValues'] is None for r in range(28, 37)), 'an unchecked seed disc gained a value it never read'
print('PASS  discs named in full but unchecked still place the next screen')

# 4) The list stops moving before the last hole: stop, do not loop.
total = 200
discs = fixture(total, REAL)
expect_stop('a list that stops moving ends the pass', '不再滚动', total, discs,
            {r: d for r, d in discs.items() if r != 200}, stuck_after=3)

# 5) Empty cells on screen are skipped: nothing is filed for the positions behind them, and they are reported
#    as unread even though no panel could be saved for them.
total = 40
discs = fixture(total, REAL)
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r not in (10, 11)}, blanks=(9, 10))
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'partial' and sorted(progress['unread']) == [10, 11] and not {10, 11} & set(rows), progress.get('unread')
print('PASS  empty cells are skipped; the holes they hide stay unread, are reported and are not filed')

# 6) The real warehouse: the readings filed so far are the known discs, the positions still missing are holes.
#    Each hole is filled with a disc unlike any other in the warehouse, built from a real reading.
real_holes = sorted(set(range(1, REAL_TOTAL + 1)) - set(REAL))
filled = dict(REAL)
if real_holes:
    from drive_disc_manager.store import fingerprint
    fillers = fixture(len(real_holes) + len(real_holes), REAL)
    taken = {fingerprint(d) for d in REAL.values()}
    spare = [d for d in fillers.values() if fingerprint(d) not in taken]
    assert len(spare) >= len(real_holes), 'not enough distinct discs to fill the real holes'
    for rank, disc in zip(real_holes, spare):
        filled[rank] = disc
    result, state, progress = resume(REAL_TOTAL, filled, REAL)
    rows = {int(k): v for k, v in progress['rows'].items()}
    assert result['status'] == 'complete' and filed(rows, filled, REAL_TOTAL), 'a real hole was filed with the wrong disc'
    screens = state['turns'] + 1
    print(f'PASS  real warehouse: {len(REAL)} known discs, {len(real_holes)} holes filled, '
          f'{len(state["clicks"])} clicks on {screens} screens')
else:
    print(f'INFO  real warehouse: all {len(REAL)} positions already read; nothing to fill')
print('resume pass OK')
