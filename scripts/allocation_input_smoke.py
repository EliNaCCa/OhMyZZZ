"""Six eligible demo discs and one unreadable alternative. No game or HTTP calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.planner import plan
from drive_disc_manager.store import defaults


def smoke():
    discs = []
    for slot, (main, value) in enumerate((('hp',550), ('atk',79), ('def',46), ('hp_',7.5),
                                          ('ice_dmg_',7.5), ('atk_',7.5)), 1):
        subs = {'crit_':2.4, 'crit_dmg_':4.8, 'pen':9}
        discs.append(dict(id=str(slot), setKey='SwingJazz', slotKey=str(slot), level=0, rarity='S',
                          mainStatKey=main, observedValues={'main':{main:value},'subs':subs},
                          substats=[dict(key=k,upgrades=0) for k in subs]))
    extra = dict(discs[3], id='extra', mainStatKey='def_')
    extra.pop('observedValues')
    z = dict(format='ZOD', dbVersion=2, version=1, characters=[], wengines=[], discs=discs+[extra],
             audit=dict(game_total=7,exported=7,delta=0))
    cfg = dict(defaults(), reserve=0, trimRecommended=True, profiles=[dict(name='冰队', agentKey='Ellen')])
    key = 'matrix/SwingJazz/4'
    rules = dict(fields={key:['hp_'], key+'/known':['hp_','def_']}, version='fixture')

    def candidates(url, profile, pool, current):
        assert len(pool) == 6
        return [dict(ids=[d['id'] for d in sorted(pool, key=lambda d:d['slot'])], outputScore=100, critRate=100)]

    def calculate(options):
        return plan(z, options, rules, {'SwingJazz':'摇摆爵士'}, candidates)

    assert calculate(dict(cfg, profiles=[]))['discs'][-1]['decision'] == '弃置候选'
    report = calculate(cfg)
    assert not report['issues'] and len(report['assignments']['冰队']['ids']) == 6
    row = report['discs'][-1]
    assert row['decision'] == '待复核' and any('主属性数值' in text for text in row['review'])
    extra.update(setKey='FutureSet', mainStatValue=12)
    row = calculate(cfg)['discs'][-1]
    assert any('套装译名' in text for text in row['review'])
    extra.update(trash=True, markState='marked')
    row = calculate(cfg)['discs'][-1]
    assert row['decision'] == '已弃置' and not row['review']


if __name__ == '__main__':
    smoke()
    print('PASS seven demo discs: missing optimizer input stays out of discard candidates')
