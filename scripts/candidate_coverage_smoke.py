"""Check all four six-disc combinations of one eight-disc sample against the pinned API."""
import argparse
from decimal import Decimal
from itertools import product
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.engine import managed, NAME, VERSION
from drive_disc_manager.optimizer import candidate_pool, select_candidate


def smoke(endpoint):
    mains = [('HP_FLAT', 2200), ('ATK_FLAT', 316), ('DEF_FLAT', 184),
             ('HP_PERCENT', 30), ('ICE_DMG', 30), ('ATK_PERCENT', 30)]
    fixed = [dict(id=str(slot), slot=slot, level=15, rarity='S', setName='谶羽之誓',
                  mainStat=dict(type=stat, value=value), subStats=[])
             for slot, (stat, value) in enumerate(mains, 1)]
    choices = [[disc] for disc in fixed]
    choices[2] = [dict(fixed[2], id='3cr', subStats=[dict(type='CRIT_RATE', value=4.8)]),
                  dict(fixed[2], id='3cd', subStats=[dict(type='CRIT_DMG', value=9.6)])]
    choices[3] = [dict(fixed[3], id='4cr', mainStat=dict(type='CRIT_RATE', value=24)),
                  dict(fixed[3], id='4cd', mainStat=dict(type='CRIT_DMG', value=48))]
    discs = [disc for slot in choices for disc in slot]
    request = dict(characterName='艾莲', roleSystem='ATTACK', characterElement='ICE',
                   baseATK=1250, baseCritRate=74, baseCritDmg=100)
    profile = dict(request=request, mainWindow='爆发', tolerancePct=0.5, windows=[
        dict(name='常态', weight=1, buffs=[]), dict(name='爆发', weight=2, buffs=[
            dict(source='队伍增益', stat='CRIT_RATE', value=15, mode='additional')])])
    found = {tuple(row['ids']): row for row in candidate_pool(endpoint, profile, discs, set())}
    # Independent expectation for this fixture: 1250 ATK with a 30% main,
    # 316 flat ATK and 30% ice damage. Neither variation changes the set bonus.
    reference = {}
    for pieces in product(*choices):
        ids = tuple(disc['id'] for disc in pieces)
        rate = Decimal(74) + (Decimal('4.8') if ids[2] == '3cr' else 0) + (24 if ids[3] == '4cr' else 0)
        damage = Decimal(100) + (Decimal('9.6') if ids[2] == '3cd' else 0) + (48 if ids[3] == '4cd' else 0)
        crit = [min(rate, 100), min(rate + 15, 100)]
        metric = (1250 * Decimal('1.3') + 316) * Decimal('1.3')
        score = sum(weight * metric * (1 + value * damage / 10000) for weight, value in zip((1, 2), crit)) / 3
        reference[ids] = (score, crit[1])
    assert set(found) == set(reference), ('missing', set(reference) - set(found), 'extra', set(found) - set(reference))
    for ids, (score, crit) in reference.items():
        assert abs(Decimal(found[ids]['rawOutput']) - score) < Decimal('0.00000001'), (ids, found[ids]['rawOutput'], score)
        assert Decimal(found[ids]['rawCritRate']) == crit
    best = max(score for score, _ in reference.values())
    eligible = [ids for ids, (score, crit) in reference.items() if crit == 100 and score >= best * Decimal('.995')]
    expected = max(eligible or reference, key=lambda ids: reference[ids][0])
    selected = select_candidate(list(found.values()), profile, set())
    assert tuple(selected['ids']) == expected
    print('PASS 8 fixture discs, 4 combinations, 2 combat windows; chosen', expected[2:4])

    # One combat window, two slot-3 pieces. +19 ATK beats +2.4 crit on damage
    # by just over 0.3%. Only the latter brings this 98% build to full crit.
    flat = dict(fixed[2], id='flat', subStats=[dict(type='ATK_FLAT', value=19)])
    cap = dict(fixed[2], id='cap', subStats=[dict(type='CRIT_RATE', value=2.4)])
    pair = [*fixed[:2], flat, cap, *fixed[3:]]
    team = dict(request=dict(request, baseCritRate=98, baseCritDmg=50))
    candidates = candidate_pool(endpoint, team, pair, set())
    assert {row['ids'][2]: Decimal(row['rawCritRate']) for row in candidates} == {'flat':98, 'cap':100}
    for tolerance, wanted in [(0.5, 'cap'), (0.3, 'flat')]:
        picked = select_candidate(candidates, dict(team, tolerancePct=tolerance), set())
        assert picked['ids'][2] == wanted
    print('PASS 7 fixture discs; 0.5% selects full crit, 0.3% selects 98% crit')

    buff = dict(member='队友', requiredMindscape=6, source='爆发增益', stat='CRIT_RATE', value=15, mode='additional')
    team = dict(request=dict(request, baseCritRate=65, combatExtraStats={'CRIT_RATE':20}),
                mainWindow='爆发', windows=[dict(name='爆发', weight=1, buffs=[buff])])
    for present, rank, rate in [(True,6,100), (True,5,85), (False,6,85)]:
        profile = dict(team, squad=[dict(agent='队友', mindscape=rank)] if present else [])
        build, = candidate_pool(endpoint, profile, fixed, set())
        assert Decimal(build['rawCritRate']) == rate
        assert build['windows'][0]['buffs'][0]['appliedMode'] == ('additional' if rate == 100 else 'off')
        # An explicitly included effect is already in the imported total. It must
        # disappear under the same conditions without being added a second time.
        base = dict(team['request'], combatExtraStats={'CRIT_RATE':35})
        imported = dict(profile, request=base, windows=[dict(name='爆发', weight=1, buffs=[dict(buff,mode='included')])])
        included, = candidate_pool(endpoint, imported, fixed, set())
        assert Decimal(included['rawCritRate']) == rate
    print('PASS 65% panel + 20% self + 15% teammate; 100% or 85% by party and mindscape')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, help='cached original release')
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        root = Path(temp) / '1'
        if args.exe:
            cache = root.parent / '_optimizer' / VERSION / NAME
            cache.parent.mkdir(parents=True)
            shutil.copyfile(args.exe, cache)
        with managed(root) as endpoint:
            smoke(endpoint)
