"""One field across three upstream revisions. No game or account data."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager import subscriptions as sub
from drive_disc_manager.store import config, read, save


def smoke(root):
    key = 'matrix/SwingJazz/4'
    cfg = config(root)
    cfg['overrides'][key] = dict(mode='only', values=['crit_'])
    save(root/'config.json', cfg)

    def update(values):
        with patch.object(sub, 'sources', return_value=({key:values}, {'Set+Main':'42'}, {key:['Set+Main']})):
            return sub.sync(root)

    def conflict(snapshot):
        return sub.current_conflicts(snapshot, config(root)['overrides']).get(key)

    update(['atk_', 'crit_'])
    one = update(['atk_', 'crit_dmg_'])
    two = update(['crit_dmg_'])
    first = conflict(one)
    latest = conflict(two)
    assert first['active'] == latest['active'] == ['crit_']
    assert latest['old'] == ['atk_', 'crit_'] and latest['remote'] == ['crit_dmg_']
    try:
        sub.resolve_conflict(root, key, first, 'remote')
    except ValueError:
        pass
    else:
        raise AssertionError('stale rule accepted')
    original = (root/'rules.json').read_bytes()
    with patch.object(sub, 'fetch', return_value='Disc 4,ATK,ATK,DEF,CRIT Rate,CRIT DMG,Anomaly Proficiency'), \
         patch.object(sub, 'sources', side_effect=lambda: sub.matrix_fields(sub.fetch('fixture'))):
        try:
            sub.sync(root)
        except ValueError:
            pass
        else:
            raise AssertionError('duplicate header accepted')
    assert (root/'rules.json').read_bytes() == original
    sub.resolve_conflict(root, key, latest, 'local')
    assert conflict(read(root/'rules.json')) is None
    assert conflict(update(['crit_dmg_'])) is None
    three = update(['crit_dmg_', 'anomProf'])
    sub.resolve_conflict(root, key, conflict(three), 'union')
    local = config(root)['overrides'][key]
    assert local['mode'] == 'add' and local['values'] == ['crit_']
    four = update(['anomProf'])
    assert conflict(four) is None and sub.effective(four['fields'][key], local) == ['anomProf', 'crit_']
    update(['anomProf', 'crit_'])
    five = update(['anomProf'])
    assert not sub.can_merge(conflict(five))
    sub.resolve_conflict(root, key, conflict(five), 'remote')
    assert key not in config(root)['overrides'] and conflict(five) is None
    from drive_disc_manager.service import preview, rule_snapshot
    pinned = rule_snapshot(root, refresh=False)
    update(['atk_'])
    sample = dict(id='d4',setKey='SwingJazz',slotKey='4',level=0,rarity='S',mainStatKey='crit_',
                  substats=[dict(key=k,upgrades=0) for k in ('crit_dmg_','atk','hp')])
    save(root/'inventory.json', dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=[sample],
                                     audit=dict(game_total=1, exported=1, delta=0)))
    report = preview(root, snapshot=pinned)
    assert report['rules'] == five['version'] != read(root/'rules.json')['version']
    assert report['traceRules'][key]['remote']==['anomProf'] and report['discs'][0]['ruleUses'][0][0]==key
    # The pending-conflict preview must also keep all three values, even after a later resolution.
    cfg['overrides'][key] = dict(mode='only',values=['crit_'])
    report = preview(root, snapshot=(cfg,one,[]))
    trace = report['traceRules'][key]
    assert trace['tabs']==['Set+Main'] and trace['conflict'] and trace['old']==['atk_', 'crit_']
    assert trace['remote']==['atk_', 'crit_dmg_'] and trace['active']==['crit_'] and trace['local']==['crit_']

    # Saving a dialog opened before the sync must compare the field under the writer lock.
    chosen = dict(mode='only', values=['crit_'])
    try:
        sub.save_field_override(root, key, seen_remote=['anomProf'], seen_local=None, override=chosen)
    except ValueError:
        pass
    else:
        raise AssertionError('editor accepted a changed upstream field')
    unrelated = config(root)
    unrelated['reserve'] = 7
    save(root/'config.json', unrelated)
    sub.save_field_override(root, key, seen_remote=['atk_'], seen_local=None, override=chosen)
    assert config(root)['reserve'] == 7 and config(root)['overrides'][key] == chosen
    try:
        sub.save_field_override(root, key, seen_remote=['atk_'], seen_local=None, override=None)
    except ValueError:
        pass
    else:
        raise AssertionError('stale editor removed a newer local override')

    stale_rules = read(root/'rules.json')
    save(root/'rules.json', dict(stale_rules, updated=0))
    def concurrent_source():
        sub.save_field_override(root, key, seen_remote=['atk_'], seen_local=chosen, override=None)
        return {key:['def_']}, {'Set+Main':'42'}, {key:['Set+Main']}
    with patch.object(sub, 'sources', side_effect=concurrent_source):
        pinned_cfg, pinned_rules, warnings = rule_snapshot(root)
    assert not warnings and key not in pinned_cfg['overrides'] and pinned_rules['fields'][key] == ['def_']

    # A deliberate local exclusion only removes this source's recommendation.
    from drive_disc_manager.planner import plan
    data = read(root/'inventory.json')
    blank = dict(fields={key:[], key+'/known':[]}, version='blank-cell')
    cfg = dict(pinned_cfg, reserve=0, overrides={})
    def row(options):
        return plan(data, options, blank, {})['discs'][0]
    assert row(cfg)['decision'] == '待复核'
    only = dict(cfg, overrides={key:dict(mode='only', values=['atk_'])})
    excluded = row(only)
    assert excluded['decision'] == '弃置候选' and '个人「只接受」规则排除此主属性' in excluded['explanation']
    assert row(dict(only, reserve=1))['decision'] == '保留'
    assert row(dict(only, pins=[excluded['fp']]))['decision'] == '保留'

    from drive_disc_manager.roster import parse_main, parse_substats
    assert parse_main(['PEN% = Elec%', 'Phys%²', 'CDMG']) == ({'pen_', 'electric_dmg_', 'physical_dmg_', 'crit_dmg_'}, False)
    assert parse_main(['Elec% / 未知属性'])[1]
    assert parse_substats(['Anomaly: AP >= ATK%', 'CRIT: CR% (until cap) >= CD%', 'Flat HP']) == {
        'anomProf', 'atk_', 'crit_', 'crit_dmg_', 'hp'}
    assert parse_substats(['hp_', 'crit_dmg_', 'anomProf']) == {'hp_', 'crit_dmg_', 'anomProf'}


if __name__ == '__main__':
    with TemporaryDirectory() as temp:
        smoke(Path(temp))
    print('PASS subscription: three-way decisions, stale edits, acknowledgement, union and failed schema')
