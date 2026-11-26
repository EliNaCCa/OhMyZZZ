"""Three profiles sharing thirteen synthetic discs. No game or player data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.allocation import arrange_profile
from drive_disc_manager.loadout import target_loadout
from drive_disc_manager.planner import plan
from drive_disc_manager.store import defaults, fingerprint


def smoke():
    discs, ids = [], {}
    for slot, (main, value) in enumerate((('hp',550), ('atk',79), ('def',46), ('hp_',7.5), ('ice_dmg_',7.5), ('atk_',7.5)), 1):
        for variant, stat in enumerate(('crit_', 'crit_dmg_', 'pen') if slot == 1 else ('crit_', 'crit_dmg_')):
            # Distinct substat fingerprints and checked S-rank initial values.
            subs = {stat:{'crit_':2.4,'crit_dmg_':4.8,'pen':9}[stat], 'anomProf':9, 'def_':4.8}
            d = dict(id=f'{slot}.{variant}', setKey='SwingJazz', slotKey=str(slot), mainStatKey=main,
                     level=0, rarity='S', markState='unmarked', lockState='unlocked', trash=False, lock=False,
                     observedValues={'main':{main:value}, 'subs':subs},
                     substats=[dict(key=key, upgrades=0) for key in subs])
            ids[slot,variant] = fingerprint(d)
            discs.append(d)
    z = dict(format='ZOD', version=1, dbVersion=2, discs=discs, characters=[], wengines=[],
             audit=dict(game_total=13, exported=13, delta=0))
    def profile(name, agent, group):
        return dict(name=name, agentKey=agent, simultaneous=group, request={'characterName':agent})
    a = profile('A 常态','Alpha','同场')
    burst = profile('A 爆发','Alpha','')
    from drive_disc_manager.optimizer import refreshed_profile
    source = dict(sourceId='alpha', agent='Alpha', requiresClaimMapping=False,
                  request=dict(characterName='Alpha', roleSystem='ATTACK', required4Set='常用套'))
    a.update(sourceId='alpha', tolerancePct=.25)
    burst.update(sourceId='alpha')
    updated, changed = refreshed_profile(a, [source])
    assert 'required4Set' in changed and updated['simultaneous']=='同场' and updated['tolerancePct']==.25
    assert 'required4Set' not in burst['request'] and refreshed_profile(updated, [source])[1]==[]
    try:
        refreshed_profile(a, [source,source])
    except ValueError as exc:
        assert '唯一' in str(exc)
    else:
        raise AssertionError('ambiguous source updated a profile')
    b = profile('B','Beta','同场')
    cfg = dict(defaults(), profiles=[a,burst,b], trimRecommended=True, reserve=0)
    rule = 'matrix/SwingJazz/4'
    rules = dict(version='fixture', fields={rule:['hp_'],rule+'/known':['hp_']})
    def build(first, rest, score):
        return dict(ids=[ids[1,first], *(ids[slot,rest] for slot in range(2,7))], outputScore=score, critRate=100)
    a_options = [build(0,0,100), build(1,0,98)]
    b_options = [build(0,1,100), build(1,1,99.9), build(2,1,90)]
    burst_option = build(2,0,101)
    def shortlist(url, p, pool, current):
        # The original shortlist misses a tiny improvement the restricted query can return.
        choices = (a_options if p['name']==a['name'] else [burst_option] if p['name']==burst['name']
                   else [b_options[0], b_options[2]] if len(pool)==13 else b_options)
        available = {d['id'] for d in pool}
        return [c for c in choices if available.issuperset(c['ids'])]
    def calculate(previous=None):
        wiki = {'character':{key:dict(code=key, EN=key) for key in ('Alpha','Beta')}}
        return plan(z,cfg,rules,{'SwingJazz':'摇摆爵士'},shortlist,wiki=wiki,previous_preview=previous)
    def check(expected_a, expected_b):
        report = calculate()
        result = report['assignments']
        assert not report['issues'] and result[a['name']]['ids']==expected_a['ids'] and result[b['name']]['ids']==expected_b['ids']
        both = [t['fp'] for t in target_loadout(cfg, report, z, a['name'])]
        solo = [t['fp'] for t in target_loadout(cfg, report, z, burst['name'])]
        assert len(set(both))==12 and solo==burst_option['ids'] and len(set(both)&set(solo))==5
        expected = set(both) | set(solo)
        assert all(r['decision']=='保留' for r in report['discs'] if r['fp'] in expected)
        return result
    result = check(a_options[0],b_options[1])
    baseline = calculate()
    cfg['reserve'] = 2
    edited = calculate(previous=baseline)
    assert edited['reusedAssignments'] and all(group['kept'] == 0 for group in edited['reserveCoverage'])
    for name in (a['name'], burst['name'], b['name']):
        recovered = target_loadout(cfg, edited, z, name)
        assert [t['fp'] for t in recovered if t['profile'] == name] == result[name]['ids']
    cfg['reserve'] = 0
    assert result[b['name']]['relativeLossPct']==0.1 and 'A 常态 占用 1 号位' in result[b['name']]['reason']
    assert result[b['name']]['rawAvailableBestOutput'] == '99.9' and result[b['name']]['availableLossPct'] == 0
    # Equal priority-C scores: choose the physical copy that leaves the strongest
    # complete lineup, even if fingerprint sorting happens to prefer the other.
    a_options[1]['outputScore'] = 100
    scarce = max((0,1), key=lambda i:a_options[i]['ids'])
    b_options[scarce]['outputScore'] = 120
    # Below the floor, the alternative cannot win an otherwise exact tie.
    a_options[1-scarce]['critRate'] = 97.59999
    check(a_options[scarce],b_options[1-scarce])
    a_options[1-scarce]['critRate'] = 97.6
    shared = check(a_options[1-scarce],b_options[scarce])
    assert '同分' in shared[a['name']]['reason']
    # An already worn piece wins an exact tie. A genuine priority-C gain wins too.
    discs[scarce]['location'] = 'Alpha'
    check(a_options[scarce],b_options[1-scarce])
    discs[scarce].pop('location')
    a_options[scarce]['rawOutput'] = '100.00000001'
    check(a_options[scarce],b_options[1-scarce])
    a_options[scarce].pop('rawOutput')
    a_options[1]['outputScore'] = 98
    b_options[scarce]['outputScore'] = (100,99.9)[scarce]
    cfg['profiles'] = arrange_profile(cfg['profiles'], 2, '同场', 1)
    assert [p['name'] for p in cfg['profiles']]==['B','A 爆发','A 常态']
    check(a_options[1],b_options[0])
    cfg['profiles'][1] = dict(burst, simultaneous='同场')
    report = calculate()
    assert '同一角色' in report['issues'][0] and not any(r['decision']=='弃置候选' for r in report['discs'])
    print('PASS higher-ranked remaining build, reorder, twelve unique simultaneous discs, alternate build retains its thirteenth disc')


if __name__ == '__main__':
    smoke()
