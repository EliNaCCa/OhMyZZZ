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
        def filed_so_far():
            saved = run_dir/'progress.json'  # what the pass had filed, whether it finished or stopped
            return json.loads(saved.read_text('utf-8')) if saved.is_file() else {}
        with patch.object(order_capture, 'read_disc', read_disc), \
             patch('drive_disc_manager.order_capture.cv2.imwrite', lambda path, image: True), \
             patch('drive_disc_manager.warehouse_sort.newest_first', lambda scanner, total, guard: True), \
             patch('drive_disc_manager.warehouse.warehouse_header', lambda ocr, screen: ('驱动仓库', total)), \
             patch('drive_disc_manager.scan_copies.scroll_thumb',
                   lambda frame: (8, 5, 5, 18) if state['top'] == 0 else (8, 60, 5, 18)), \
             patch('drive_disc_manager.manual_equip.guarded_grid',
                   lambda grid, guard: _identity(grid, scanner, controller=scanner.ctx.controller)):
            try:
                result = order_capture.capture(scanner, total, lambda: None, run_dir, root, seed)
            except ValueError as exc:
                exc.filed = filed_so_far()    # let a test look at what a stopped pass had filed
                raise
        progress = filed_so_far()
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


def placed_right(rows, discs, total):
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
assert placed_right(rows, discs, total), 'a reading was filed under the wrong position'
print(f'PASS  {len(holes)} holes read once each, {total} positions filed, {len(state["clicks"])} clicks on {screens} screens')

# 1b) Two identical discs in the top row: one reading cannot place the screen, so a second is read.
total = 120
discs = fixture(total, REAL)
for rank in (28, 32):                       # both on the top row of the second screen
    discs[rank] = dict(discs[29], setKey=discs[29]['setKey'])
    discs[rank]['substats'] = [dict(s) for s in discs[29]['substats']]
result, state, progress = resume(total, discs, {r: d for r, d in discs.items() if r != 40})
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and placed_right(rows, discs, total), 'misplaced with duplicate discs'
print('PASS  duplicate discs in the top row are resolved by a second reading, nothing misplaced')

# 2) A hole whose every line is named but whose values could not be checked is filed for the order record
#    with its values left empty, so it can be matched against the network inventory. It does not count as read:
#    it stays a hole, and when reading it again settles nothing the pass stops and says so instead of
#    reporting the warehouse complete. Every later check against the physical disc refuses it.
total = 40
discs = fixture(total, REAL)
try:
    resume(total, discs, {r: d for r, d in discs.items() if r != 30}, unreadable={30})
except ValueError as exc:
    assert '数值未核对' in str(exc) and '1 张' in str(exc) and '待读 1' not in str(exc), exc
    saved = exc.filed
    print('PASS  a disc named in full whose value stays unchecked stops the pass and is reported · ' + str(exc))
else:
    raise AssertionError('a disc with an unchecked value was reported as read')
rows = {int(k): v for k, v in saved['rows'].items()}
assert len(rows) == total and 30 in rows and rows[30]['observedValues'] is None and saved['unread'] == [30], (len(rows), saved.get('unread'))
assert all(rows[r].get('observedValues') for r in rows if r != 30), 'a checked reading lost its values'
assert [s['key'] for s in rows[30]['substats']] == [s['key'] for s in discs[30]['substats']]
from drive_disc_manager.stat_reading import checked_values
assert checked_values(rows[30], 1) is None, 'an unverified reading passed the value check'
print('PASS  before stopping it filed that disc by its lines at its position, values empty')

# 2c) A disc filed unchecked by an earlier pass is tried again, and once its values read the pass completes.
total = 40
discs = fixture(total, REAL)
seed = dict(discs)
seed[30] = dict(discs[30], observedValues=None)            # filed unchecked by an earlier pass
result, state, progress = resume(total, discs, seed)
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and placed_right(rows, discs, total) and rows[30].get('observedValues'), result
print('PASS  a disc filed unchecked by an earlier pass is read again and completed once its values come')

# 2b) A hole whose lines themselves cannot be read is never filed.
total = 40
discs = fixture(total, REAL)
try:
    resume(total, discs, {r: d for r, d in discs.items() if r != 30}, invisible={30})
except ValueError as exc:
    assert '1 张没有读出可用内容' in str(exc) and '数值未核对' not in str(exc), exc
    saved = exc.filed
    print('PASS  a slot that gives nothing readable stops the pass and is reported as such · ' + str(exc))
else:
    raise AssertionError('a slot that gave nothing readable was reported as read')
assert 30 not in {int(k) for k in saved['rows']} and saved['unread'] == [30], saved.get('unread')
print('PASS  nothing is filed for a slot whose lines could not be read')

# 3) A screen where no disc can be matched: stop, do not guess. Nothing on the top row of the very first
#    screen gives a reading, so there is nothing known to place it by.
total = 120
discs = fixture(total, REAL)
expect_stop('a screen with nothing to place it by stops the pass', '没有对上已知的盘', total, discs,
            {r: d for r, d in discs.items() if r > 9}, invisible=set(range(1, 10)))

# (No separate case for a later screen that cannot be placed. Screens overlap by one row, so whatever the
#  previous screen read at its bottom is the next screen's top row and places it. A later screen can only fail
#  to be placed when its top row gave nothing readable on the previous screen too, and case 2b already stops
#  the pass there, one screen earlier.)

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
assert result['status'] == 'complete' and placed_right(rows, discs, total), 'a screen was not placed by unchecked discs'
assert all(rows[r].get('observedValues') for r in range(28, 37)), 'an unchecked disc was not read again'
print('PASS  discs named in full but unchecked place the next screen, and are read again until their values come')

# 4) The list stops moving before the last hole: stop, do not loop.
total = 200
discs = fixture(total, REAL)
expect_stop('a list that stops moving ends the pass', '不再滚动', total, discs,
            {r: d for r, d in discs.items() if r != 200}, stuck_after=3)

# 5) Empty cells on screen are skipped: nothing is filed for the positions behind them. Reading them again
#    cannot help, so the pass stops on that screen and reports them, with no panel saved for them.
total = 40
discs = fixture(total, REAL)
try:
    resume(total, discs, {r: d for r, d in discs.items() if r not in (10, 11)}, blanks=(9, 10))
except ValueError as exc:
    assert '2 张没有读出可用内容' in str(exc), exc
    saved = exc.filed
    print('PASS  empty cells are skipped and reported · ' + str(exc))
else:
    raise AssertionError('empty cells were reported as read')
assert not {10, 11} & {int(k) for k in saved['rows']} and sorted(saved['unread']) == [10, 11], saved.get('unread')
print('PASS  nothing is filed for the positions behind empty cells')

# 6) The real warehouse, replayed against itself: the filed readings are the seed. Positions with no reading at
#    all get a disc unlike any other in the warehouse; positions filed without checked values keep their own disc
#    and read it again, as a live pass would. Everything must end up placed right with values everywhere.
from drive_disc_manager.order_capture import usable
from drive_disc_manager.store import fingerprint
missing = sorted(r for r in range(1, REAL_TOTAL + 1) if r not in REAL)
unchecked = sorted(r for r, d in REAL.items() if not usable(d))
truth = dict(REAL)
if missing:
    # The value of a disc filed unchecked is not known here, so the simulated warehouse shows it with values.
    fillers = fixture(len(missing) + len(missing), REAL)
    taken = {fingerprint(d) for d in REAL.values()}
    spare = [d for d in fillers.values() if fingerprint(d) not in taken]
    assert len(spare) >= len(missing), 'not enough distinct discs to fill the real holes'
    for rank, disc in zip(missing, spare):
        truth[rank] = disc
for rank in unchecked:
    # Give the simulated warehouse well-formed values for that disc. It must be a copy: the seed keeps the
    # disc as filed, without values, or there would be nothing left for the pass to read.
    source = REAL[rank]
    from drive_disc_manager.optimizer import STATS, exported_s_main
    truth[rank] = dict(source, observedValues=dict(
        main={source['mainStatKey']: float(exported_s_main(source['mainStatKey'], source['level']))},
        subs={line['key']: float(STATS[line['key']][1] * line['upgrades']) for line in source['substats']}))
assert [r for r, d in REAL.items() if not usable(d)] == unchecked, 'the seed was altered'
result, state, progress = resume(REAL_TOTAL, truth, REAL)
rows = {int(k): v for k, v in progress['rows'].items()}
assert result['status'] == 'complete' and placed_right(rows, truth, REAL_TOTAL), 'a real disc was filed under the wrong position'
assert all(usable(r) for r in rows.values()), 'a position is still without checked values'
assert not progress.get('unread'), progress.get('unread')
screens = state['turns'] + 1
print(f'PASS  real warehouse: {len(REAL)} filed readings as the seed, {len(missing)} positions without a reading, '
      f'{len(unchecked)} filed without checked values, all {REAL_TOTAL} placed with values, {len(state["clicks"])} clicks on {screens} screens')
print('resume pass OK')
