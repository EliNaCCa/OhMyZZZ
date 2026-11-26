"""Six eligible demo discs and one unreadable alternative. No game or HTTP calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.planner import plan
from drive_disc_manager.store import defaults, fingerprint


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
    cfg = dict(defaults(), reserve=0, trimRecommended=True, profiles=[dict(name='冰队', request=dict(characterName='艾莲'))])
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
    # A legacy link can establish the current wearer for display, but only an
    # explicitly saved, matching binding may authorize an actual equip.
    wiki = {'character': {'1': dict(code='Ellen', CHS='艾莲', EN='Ellen Joe')}}
    team = dict(name='冰队', sourceId='demo', request=dict(characterName='艾莲'), agentKey='')
    discs[0].update(ownerState='worn', location='Ellen')
    def compare(url, profile, pool, current):
        assert current == {fingerprint(discs[0])}
        return candidates(url, profile, pool, current)
    def bound(profiles, dictionary=wiki, previous=None):
        return plan(z, dict(cfg, profiles=profiles), rules, {'SwingJazz':'摇摆爵士'}, compare,
                    wiki=dictionary, previous_preview=previous)
    legacy = bound([team])
    assert not legacy['issues'] and len(legacy['assignments']['冰队']['ids']) == 6
    assert not team['agentKey']
    valid = dict(team, agentKey='Ellen')
    cached = bound([valid])
    drift = {'character': {'1': dict(code='NewEllen', CHS='艾莲', EN='Ellen Joe')}}
    for invalid in [bound([dict(team, agentKey='Anby')]), bound([valid], drift, cached)]:
        assert invalid['issues'] and '绑定' in invalid['assignments']['冰队']['error']
        assert not invalid['reusedAssignments'] and '弃置候选' not in invalid['counts']
    a, b = dict(team, simultaneous='同队'), dict(team, name='艾莲爆发', simultaneous='同队',
                                                request=dict(characterName='Ellen Joe'))
    assert '同一角色' in bound([a, b])['assignments']['冰队']['error']
    assert not bound([dict(a, simultaneous=''), dict(b, simultaneous='')])['issues']
    discs[0].pop('ownerState'); discs[0].pop('location')
    row = report['discs'][-1]
    assert row['decision'] == '待复核' and any('主属性数值' in text for text in row['review'])
    extra.update(setKey='FutureSet', mainStatValue=12)
    row = calculate(cfg)['discs'][-1]
    assert any('套装译名' in text for text in row['review'])
    extra.update(setKey='SwingJazz', mainStatValue='12%')
    assert calculate(cfg)['discs'][-1]['decision'] == '待复核'
    extra.update(slotKey='1', mainStatValue=12)
    row = calculate(cfg)['discs'][-1]
    assert row['decision'] == '待复核' and any('1 号位不能出现' in text for text in row['review'])
    extra.update(slotKey='4', level=15, mainStatValue=48)
    report = calculate(cfg)
    assert not report['issues'] and '强化档数与等级不符' in report['discs'][-1]['review']
    extra.update(trash=True, markState='marked')
    row = calculate(cfg)['discs'][-1]
    assert row['decision'] == '已弃置' and not row['review']


if __name__ == '__main__':
    smoke()
    print('PASS seven demo discs: missing optimizer input stays out of discard candidates')
