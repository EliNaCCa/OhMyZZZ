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
ap.add_argument('--seed', type=Path, help='a progress.json of real readings to use as the known discs')
home = ap.parse_args().home
seed_file = ap.parse_args().seed
repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
import numpy as np
from one_dragon.base.geometry.point import Point


def reading(set_key, slot, level, main, subs):
    """A disc reading shaped like the scanner's parse result."""
    return dict(id='sim', setKey=set_key, slotKey=str(slot), level=level, rarity='S', mainStatKey=main,
                substats=[dict(key=k, upgrades=1) for k in subs],
                observedValues=dict(main={main: 7.5}, subs={k: 9.0 for k in subs}), observedGrade='S',
                lockState='unlocked', markState='unmarked', ownerState='bag')


def warehouse(total, discs, unreadable, blanks=(), stuck_after=None):
    """A 9-column, 4-row window over `discs` ({position: reading}), moved by scroll(n rows)."""
    state = dict(top=0, clicks=[], turns=0)

    def shown(offset):
        rank = state['top']*9 + offset + 1
        if rank not in discs:
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


def resume(total, discs, seed, unreadable=(), blanks=(), stuck_after=None):
    from drive_disc_manager import order_capture
    scanner, state, shown = warehouse(total, discs, unreadable, blanks, stuck_after)
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


def fixture(total):
    """A warehouse of pairwise distinct discs, so that a reading identifies exactly one position."""
    sets = ['SwingJazz', 'WoodpeckerElectro', 'SoulRock', 'AstralVoice', 'Branch&BladeSong', 'KingoftheSummit',
            "Dawn'sBloom", 'MoonlightLullaby', 'HormonePunk', 'FangedMetal', 'PufferElectro', 'PhaethonsMelody']
    out = {}
    for rank in range(1, total + 1):
        main = ['hp', 'atk', 'def', 'crit_dmg_', 'atk_', 'anomProf'][(rank - 1) % 6]
        # Roll counts 1..5 on three lines give 125 combinations; combined with slot, set and level that is unique.
        a, b = (rank - 1) % 5 + 1, ((rank - 1) // 5) % 5 + 1
        c = ((rank - 1) // 25) % 5 + 1
        subs = {'crit_': a, 'atk_': b, 'pen': c}
        disc = reading(sets[(rank // 30) % len(sets)], (rank - 1) % 6 + 1, 15 if rank % 5 == 0 else 0, main, subs)
        disc['substats'] = [dict(key=k, upgrades=v) for k, v in subs.items()]
        out[rank] = disc
    return out


def distinct(discs):
    from drive_disc_manager.store import fingerprint
    return len({fingerprint(d) for d in discs.values()}) == len(discs)


def filed(rows, discs, total):
    """True when every position holds exactly the disc the simulated warehouse has there."""
    from drive_disc_manager.store import fingerprint
    return set(rows) == set(range(1, total + 1)) and all(fingerprint(rows[r]) == fingerprint(discs[r]) for r in range(1, total + 1))


# 1) Holes at the very top, across a page boundary, in the middle and at the very end.
total = 200
discs = fixture(total)
assert distinct(discs), 'the fixture must not contain two discs with the same stats'
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
discs = fixture(total)
for rank in (28, 32):                       # both on the top row of the second screen
    discs[rank] = dict(discs[29], setKey=discs[29]['setKey'])
    discs[rank]['substats'] = [dict(s) for s in discs[29]['substats']]
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 40})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and filed(rows, discs, total), 'misplaced with duplicate discs'
print('PASS  duplicate discs in the top row are resolved by a second reading, nothing misplaced')

# 2) A hole whose values cannot be read keeps its screenshot and is reported, never filed under a guess.
total = 40
discs = fixture(total)
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 30}, unreadable={30})
assert result['status'] == 'partial' and progress['unread'] == [30] and 30 not in {int(k) for k in progress['rows']}
print('PASS  an unreadable hole is reported with its panel and is not filed')

# 3) A screen where no disc can be matched: stop, do not guess. The top row of the very first screen is
#    all unreadable holes, so there is nothing known to place it by.
total = 120
discs = fixture(total)
expect_stop('a screen with nothing to place it by stops the pass', '没有对上已知的盘', total, discs,
            {r: d for r, d in discs.items() if r > 9}, unreadable=set(range(1, 10)))

# 3b) The same on a later screen. Screens overlap by one row, and a hole read on one screen can place the next,
#     so the screen that must stop is one whose every position is an unreadable hole.
total = 120
discs = fixture(total)
expect_stop('a later screen with nothing known stops the pass', '没有对上已知的盘', total, discs,
            {r: d for r, d in discs.items() if not 28 <= r <= 91}, unreadable=set(range(28, 92)))

# 4) The list stops moving before the last hole: stop, do not loop.
total = 200
discs = fixture(total)
expect_stop('a list that stops moving ends the pass', '不再滚动', total, discs,
            {r: d for r, d in discs.items() if r != 200}, stuck_after=3)

# 5) Empty cells on screen are skipped and are not counted as readings of the positions behind them.
total = 40
discs = fixture(total)
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r not in (10, 11)}, blanks=(9, 10))
assert result['status'] == 'partial' and sorted(progress['unread']) == [] and 10 not in {int(k) for k in progress['rows']}
print('PASS  empty cells are skipped; the holes they hide stay unread and are not filed')

# 6) Today's real readings as the known discs; the 102 real holes are filled with discs that read back cleanly.
if seed_file:
    saved = json.loads(seed_file.read_text('utf-8'))
    real = {int(k): v for k, v in saved['rows'].items()}
    total = saved['total']
    real_holes = sorted(set(range(1, total + 1)) - set(real))
    # Distinct fillers: roll counts vary so that no filler equals a neighbour or another filler.
    filled = dict(real)
    for i, rank in enumerate(real_holes):
        a, b, c = i % 5 + 1, (i // 5) % 5 + 1, (i // 25) % 5 + 1
        disc = reading('SwingJazz', rank % 6 + 1, 0, 'atk_', {'crit_': a, 'hp_': b, 'pen': c})
        disc['substats'] = [dict(key=k, upgrades=v) for k, v in {'crit_': a, 'hp_': b, 'pen': c}.items()]
        filled[rank] = disc
    result, state, progress = resume(total, filled, real)
    rows = {int(k): v for k, v in progress['rows'].items()}
    assert result['status'] == 'complete' and filed(rows, filled, total), 'a real hole was filed with the wrong disc'
    screens = state['turns'] + 1
    print(f'PASS  real warehouse: {len(real)} known discs, {len(real_holes)} holes filled, '
          f'{len(state["clicks"])} clicks on {screens} screens')
print('resume pass OK')
