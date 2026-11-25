"""Windows smoke: save a target, restart, allocate six demo discs, check exit."""
import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.engine import NAME, VERSION, managed
from drive_disc_manager.optimizer import loopback_json, saved_targets, candidate_pool, select_candidate
from drive_disc_manager.service import preview
from drive_disc_manager.store import defaults, save


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
            threshold = dict(request=dict(target,baseCritRate=97.59999), tolerancePct=.5)
            chosen = select_candidate(candidate_pool(endpoint,threshold,pieces,set()),threshold,set())
            assert chosen['bestNearFullOutput'] is None and chosen['critRate']==97.59999
            rupture = dict(request=dict(target,roleSystem='RUPTURE',baseHP=7000),tolerancePct=.5)
            chosen = select_candidate(candidate_pool(endpoint,rupture,pieces,set()),rupture,set())
            assert chosen['metric']=='贯穿力' and chosen['metricValue']>0
            print('PASS equal API scores distinguished, raw crit threshold respected, rupture index verified')
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
        report = preview(root, refresh=False)
        result = report['assignments']['演示队']
        assert len(set(result.get('ids', ()))) == 6 and result['outputScore'] > 0, (result, report['issues'])
        print('PASS saved target survived restart; preview allocated six demo slots; managed processes exited')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, help='locally cached original release executable')
    smoke(parser.parse_args().exe)
