"""Windows smoke: save a target, restart, allocate six demo discs, check exit."""
import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.engine import NAME, VERSION, managed
from drive_disc_manager.optimizer import loopback_json, saved_targets, candidate_pool, select_candidate
from drive_disc_manager.service import preview
from drive_disc_manager.store import defaults, save
from drive_disc_manager.allocation import assign_group


def smoke_role_targets(endpoint, pieces):
    current = {disc['id'] for disc in pieces}
    impact = dict(pieces[5], id='impact', mainStat=dict(type='IMPACT', value=18))
    request = dict(characterName='莱卡恩', roleSystem='STUN', mode='UTILITY_BALANCE',
                   baseImpact=100, baseEnergyRegen=1.2, baseATK=1000, targetImpact=110)

    def choose(**overrides):
        profile = dict(name='职业面板冒烟', request=dict(request, **overrides))
        return assign_group([(profile, current)], pieces+[impact], endpoint, candidate_pool)[0][1]

    chosen = choose()
    assert chosen['panelRows'][0] == ('冲击力', '面板下限', 110, 100, 118, 18, 8)
    assert chosen['ids'][-1] == 'impact'
    try:
        choose(targetImpact=140)
    except ValueError as exc:
        assert '当前装备 · 冲击力 100 / 目标 140' in str(exc), exc
    else:
        raise AssertionError('A diagnostic baseline became a selectable build')
    strict = choose(targetImpact=140, mode='STRICT_TARGETS', targetTypes={'IMPACT':'CAP'}, targetPriorities={'IMPACT':1})
    assert strict['panelRows'][0] == ('冲击力', '优先级 1 · 最低目标', 140, 100, 118, 18, -22)

    anomaly = dict(roleSystem='ANOMALY', mode='ANOMALY_AP', targetImpact=0,
                   extraStats={'ANOMALY_PROFICIENCY':90}, combatExtraStats={'ANOMALY_PROFICIENCY':30})
    raw = loopback_json(endpoint, '/api/optimize', dict(request, **dict(anomaly, discs=pieces)))['results'][0]
    assert choose(**anomaly)['panel']['ANOMALY_PROFICIENCY'] == raw['stats']['ANOMALY_PROFICIENCY'] == 120
    assert raw['combatStats']['ANOMALY_PROFICIENCY'] == 200
    print('PASS role targets: worn vs selected, hard vs strict, panel vs combat proficiency')


def smoke(source):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)/'1'
        if source:
            cached = root.parent/'_optimizer'/VERSION/NAME
            cached.parent.mkdir(parents=True)
            shutil.copyfile(source, cached)
        target = dict(characterName='艾莲', roleSystem='ATTACK', characterElement='ICE',
                      baseCritRate=65, baseCritDmg=50, baseATK=1250)
        with managed(root) as endpoint:
            first = endpoint.proc
            loopback_json(endpoint, '/api/character-targets', dict(plans=[dict(
                id='smoke', characterName='艾莲', request=target)]))
        assert not first.is_running()
        with managed(root) as endpoint:
            second = endpoint.proc
            saved, = saved_targets(endpoint)
            assert saved['sourceId'] == 'smoke' and not saved['requiresClaimMapping']
            # Two legal slot-3 alternatives. The pinned server displays identical
            # output when the only difference is a CD roll at a tiny crit rate.
            mains = [('HP_FLAT',2200),('ATK_FLAT',316),('DEF_FLAT',184),
                     ('HP_PERCENT',30),('ICE_DMG',30),('ATK_PERCENT',30)]
            pieces = [dict(id=str(slot),slot=slot,level=15,rarity='S',setName='谶羽之誓',
                           mainStat=dict(type=stat,value=value),subStats=[])
                      for slot,(stat,value) in enumerate(mains,1)]
            other = dict(pieces[2],id='cd',subStats=[dict(type='CRIT_DMG',value=4.8)])
            weak = dict(target, baseCritRate=0.00001)
            result = loopback_json(endpoint,'/api/optimize',dict(weak,discs=pieces+[other]))
            assert len(result['results'])==2 and len({r['outputScore'] for r in result['results']})==1
            current = {d['id'] for d in pieces}
            profile = dict(request=weak, tolerancePct=0)
            pool = candidate_pool(endpoint,profile,pieces+[other],current)
            winner = select_candidate(pool,profile,current)
            assert winner['ids'][2]=='cd' and len({r['rawOutput'] for r in pool})==2
            assert winner['wornOutput'] == next(r['rawOutput'] for r in pool if set(r['ids']) == current)
            assert winner['optimizerScope']==dict(submitted=7, calls=2, evaluated=4, atReturnLimit=False)
            threshold = dict(request=dict(target,baseCritRate=97.59999), tolerancePct=.5)
            chosen = select_candidate(candidate_pool(endpoint,threshold,pieces,set()),threshold,set())
            assert chosen['bestNearFullOutput'] is None and chosen['critRate']==97.59999
            capped = dict(request=dict(target,baseCritRate=100), tolerancePct=.5)
            over = dict(pieces[2], id='over', subStats=[dict(type='CRIT_RATE',value=9.6)])
            tied = select_candidate(candidate_pool(endpoint,capped,pieces+[over],current),capped,current)
            assert tied['ids'][2]=='3' and tied['overflowCritRate']==0
            over['subStats'].append(dict(type='ATK_FLAT',value=19))
            stronger = select_candidate(candidate_pool(endpoint,capped,pieces+[over],current),capped,current)
            assert stronger['ids'][2]=='over' and stronger['critRate']==100
            assert stronger['uncappedCritRate']==109.6 and stronger['overflowCritRate']==9.6
            assert stronger['outputScore'] > tied['outputScore']
            # Current slot 4 is HP. Require crit damage to exclude it from the
            # candidate pool, then measure the worn set under the same two windows.
            cd4 = dict(pieces[3], id='cd4', mainStat=dict(type='CRIT_DMG', value=48))
            constrained = dict(request=dict(target, slotAllowedMainStats={'4':['CRIT_DMG']}), mainWindow='爆发',
                               windows=[dict(name='常态',weight=1,buffs=[]), dict(name='爆发',weight=2,
                                   buffs=[dict(source='窗口加成',stat='CRIT_RATE',value=15,mode='additional')])])
            new, = candidate_pool(endpoint,constrained,pieces+[cd4],current)
            reference, = candidate_pool(endpoint,dict(constrained,request=target),pieces,set())
            assert new['ids'][3]=='cd4' and new['wornOutput']==reference['rawOutput']
            rupture = dict(request=dict(target,roleSystem='RUPTURE',baseHP=7000),tolerancePct=.5)
            chosen = select_candidate(candidate_pool(endpoint,rupture,pieces,set()),rupture,set())
            assert chosen['metric']=='贯穿力' and chosen['metricValue']>0
            print('PASS equal API scores distinguished, raw crit threshold respected, rupture index verified')
            smoke_role_targets(endpoint, pieces)
        assert not second.is_running()
        cfg = defaults()
        cfg['profiles'] = [dict(name='演示队', agentKey='Ellen', request=saved['request'])]
        save(root/'config.json', cfg)
        discs = []
        for i, main in enumerate(('hp','atk','def','crit_','ice_dmg_','atk_'), 1):
            subs = [dict(key=k, upgrades=u) for k,u in zip(('crit_dmg_', 'pen', 'hp_' , 'anomProf'), (2,1,1,1))]
            discs.append(dict(id=str(i),setKey='FeatheredFate',slotKey=str(i),level=15,rarity='S',
                              mainStatKey=main,substats=subs,lock=False,trash=False,location=''))
        # Recorded +3 slot 5 among five demo +15 pieces (this fixture uses zero-based upgrades).
        subs = {'atk':19, 'crit_':2.4, 'def_':4.8, 'hp':112}
        discs[4].update(level=3, mainStatKey='atk_', substats=[dict(key=k,upgrades=0) for k in subs],
                        observedValues={'main':{'atk_':12}, 'subs':subs})
        save(root/'inventory.json', dict(format='ZOD', dbVersion=2, version=1, discs=discs,
             characters=[], wengines=[], audit=dict(game_total=6,exported=6,delta=0)))
        wiki = root/'wiki.json'
        save(wiki, {'character': {'1':dict(code='Ellen', EN='Ellen Joe', CHS='艾莲')}})
        report = preview(root, wiki, refresh=False)
        result = report['assignments']['演示队']
        assert len(set(result.get('ids', ()))) == 6 and result['outputScore'] > 0, (result, report['issues'])
        cfg['reserve_rules'] = [dict(set='FeatheredFate', purpose='备用精通', stats=['anomProf'], count=2)]
        save(root/'config.json', cfg)
        with patch('drive_disc_manager.engine.managed', side_effect=AssertionError('unexpected optimizer launch')) as launch:
            adjusted = preview(root, wiki, refresh=False, reuse_allocations=True)
            launch.assert_not_called()
        assert adjusted['reusedAssignments'] and adjusted['assignments'] == report['assignments']
        assert adjusted['configuration'] != report['configuration']
        assert all(row['decision'] == '保留' and not any(r.startswith('备用 · ') for r in row['reasons'])
                   for row in adjusted['discs'])
        # The same six IDs under a different team tolerance still need a new allocation.
        cfg['profiles'][0]['tolerancePct'] = 0
        save(root/'config.json', cfg)
        updated = preview(root, wiki, refresh=False, reuse_allocations=True)
        assert not updated['reusedAssignments'] and updated['allocationKey'] != adjusted['allocationKey'] and not updated['issues']
        print('PASS saved target survived restart; preview allocated six demo slots; managed processes exited')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, help='locally cached original release executable')
    smoke(parser.parse_args().exe)
