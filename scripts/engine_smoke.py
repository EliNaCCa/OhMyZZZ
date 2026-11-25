"""Windows smoke: save a target, restart, allocate six demo discs, check exit."""
import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.engine import NAME, VERSION, managed
from drive_disc_manager.optimizer import loopback_json, saved_targets
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
