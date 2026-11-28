"""Screen positions from disc readings alone. No game window, OneDragon or account required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.order_positions import locate, wanted


def disc(set_key, level, main='atk_', subs=('crit_', 'atk_', 'pen'), rolls=(1, 1, 1)):
    return dict(id='x', setKey=set_key, slotKey='4', level=level, rarity='S', mainStatKey=main,
                substats=[dict(key=k, upgrades=r) for k, r in zip(subs, rolls)])


sets = ['SwingJazz', 'WoodpeckerElectro', 'SoulRock', 'AstralVoice', 'Branch&BladeSong', 'KingoftheSummit',
        "Dawn'sBloom", 'MoonlightLullaby', 'HormonePunk', 'FangedMetal']
# 100 distinct discs: position decides the set, level and roll counts.
known = {rank: disc(sets[rank % 10], rank % 16, rolls=(rank % 5 + 1, rank // 5 % 5 + 1, rank // 25 % 5 + 1))
         for rank in range(1, 101)}
assert len({str(sorted(d['substats'], key=lambda s: s['key'])) + d['setKey'] + str(d['level']) for d in known.values()}) == 100

# One disc seen in the top-left cell fixes the screen.
assert locate({0: known[28]}, known) == 27
assert locate({3: known[31]}, known) == 27
# Several discs must agree.
assert locate({0: known[28], 4: known[32], 8: known[36]}, known) == 27
# A reading from any row works, since offsets count from the top of the screen.
assert locate({9: known[37], 13: known[41]}, known) == 27
# Nothing known, or an unknown disc: no position.
assert locate({}, known) is None
assert locate({0: disc('NoSuchSet', 0)}, known) is None
assert locate({0: disc('SwingJazz', 15, rolls=(5, 5, 5))}, known) is None
# Two readings that disagree do not place the screen.
assert locate({0: known[28], 1: known[60]}, known) is None
# Two identical discs at different positions give two equally supported starts unless another reading decides.
known[200] = dict(known[28])
assert locate({0: known[28]}, known) is None
assert locate({0: known[28], 5: known[33]}, known) == 27
del known[200]
# The end of the list: the last screen starts where the list stopped, not 27 further on.
assert locate({0: known[73], 8: known[81]}, known) == 72
# A disc seen at an offset that would put the screen before the first position is not accepted.
assert locate({0: known[1]}, known) == 0
assert wanted(0, range(36), {1, 9, 36, 37}, 100) == [0, 8, 35]
assert wanted(72, range(36), {100}, 100) == [27]
assert wanted(72, range(36), {100, 101}, 100) == [27]        # 101 is beyond the warehouse
assert wanted(72, range(36), set(), 100) == []
print('PASS locate: one reading places a screen, unknown or conflicting readings place nothing')
print('PASS wanted: only holes on screen, never past the end of the list')
