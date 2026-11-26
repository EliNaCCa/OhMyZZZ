"""One field across three upstream revisions. No game or account data."""
import sys
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager import subscriptions as sub
from drive_disc_manager.store import config, read, save


def guide_smoke(root):
    cards = '''
,Roxy Ifrita Pryce,,
,Last Updated:,,
,,Main Stats,Sub Stats
,,ER% Dics 6,ATK%
,Anton Ivanov,,
,Last Updated:,,
,,Main Stats,Sub Stats
,,CR%/CD%Disc 4,
,Ju Fufu,,
,Last Updated:,,
,,Main Stats,Sub Stats
,,ATK% > CR% Disc 4¹,CR%
,,Main Stats,Sub Stats
,,Impact% Disc 6,
,,Generic Team Example,
'''
    fields = sub.guide_fields(cards)
    expected = {'guide/Roxy Ifrita Pryce/slot6':{'energyRegen_'},
                'guide/Anton Ivanov/slot4':{'crit_', 'crit_dmg_'},
                'guide/Ju Fufu/slot4':{'atk_', 'crit_'}, 'guide/Ju Fufu/slot6':{'impact'}}
    for key, stats in expected.items():
        assert sub.accepted_terms(key, fields.get(key)) == stats, key
    key = 'guide/Ju Fufu/slot4'
    cfg = config(root)
    cfg['overrides'] = {key:dict(mode='only', values=['crit_'])}
    save(root/'config.json', cfg)
    tabs = {'Fire':'42'}
    def update(previous, fields, origins):
        return sub.merge(previous, fields, cfg['overrides'], tabs, origins)
    base = update({}, {key:fields[key]}, {key:['Fire']})
    save(root/'rules.json', base)
    before = (root/'rules.json').read_bytes()
    broken = cards.replace('Main Stats,Sub Stats\n,,CR%/CD%Disc', 'Main,Sub\n,,CR%/CD%Disc')
    with patch.object(sub, 'sources', side_effect=lambda: (sub.guide_fields(broken), tabs, {})):
        try:
            sub.sync(root)
        except ValueError as exc:
            assert 'Anton Ivanov' in str(exc)
        else:
            raise AssertionError('published a guide with a missing role header')
    assert (root/'rules.json').read_bytes() == before
    assert key not in sub.guide_fields(cards.replace('ATK% > CR% Disc 4¹', ''))
    alternate = update(base, base['fields'], {key:['Other']})
    assert alternate['version'] != base['version'] and not alternate['conflicts']
    removed = update(base, {'build/Ju Fufu/sets4':['SwingJazz']}, {})
    save(root/'rules.json', removed)
    conflict = sub.current_conflicts(removed, cfg['overrides'])[key]
    assert conflict['old'] == fields[key] and conflict['remote'] is None
    assert conflict['active'] == ['crit_'] and sub.field_tabs(removed, key) == ['Fire']
    from drive_disc_manager.planner import plan
    sample = dict(id='d4',setKey='SwingJazz',slotKey='4',level=0,rarity='S',mainStatKey='crit_',
                  substats=[dict(key=k,upgrades=0) for k in ('crit_dmg_','atk','hp')])
    inventory = dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=[sample],
                     audit=dict(game_total=1, exported=1, delta=0))
    report = plan(inventory, dict(cfg, futureAgents=['jufufu'], reserve=0, trimRecommended=False), removed, {})
    assert report['discs'][0]['decision'] == '保留' and (key,'Ju Fufu','主属性符合') in report['discs'][0]['ruleUses']
    entry = report['traceRules'][key]
    assert entry['tabs'] == ['Fire'] and entry['old'] == fields[key] and entry['active'] == ['crit_']
    sub.resolve_conflict(root, key, conflict, 'local')
    cfg = config(root)
    same = update(removed, removed['fields'], {})
    assert not sub.current_conflicts(same, cfg['overrides']) and sub.field_tabs(same, key) == ['Fire']
    restored = update(same, {key:fields[key]}, {key:['Updated Fire']})
    assert sub.field_tabs(restored, key) == ['Updated Fire']


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
    # Short downloads and broken CSV must not replace the last successful revision.
    header = 'Name,Disc 4,Disc 5,Disc 6,Substats,4pc_0,All 2pc_0\n'
    assert sub.build_fields(header+'Ellen Joe,,,,,,')['build/Ellen Joe/sets2'] == []
    save(root/'rules.json', dict(read(root/'rules.json'), updated=0))
    snapshot_bytes = (root/'rules.json').read_bytes()
    truncated = BytesIO(b'partial')
    truncated.headers = {'Content-Length':'10'}
    for downloader, parser in [(truncated, 'fetch'), (IncompleteRead(b''), 'fetch'),
                               ('Ellen Joe,CR%', 'parse'), ('"Ellen Joe', 'parse')]:
        if parser == 'fetch':
            mock_args = {'side_effect':downloader} if isinstance(downloader, Exception) else {'return_value':downloader}
            mocked = patch.object(sub.urllib.request, 'urlopen', **mock_args)
            source = lambda: sub.fetch('fixture')
        else:
            mocked = patch.object(sub, 'fetch', return_value=header+downloader)
            source = lambda: sub.build_fields(sub.fetch('fixture'))
        with mocked, patch.object(sub, 'sources', side_effect=source):
            _, last_good, warnings = rule_snapshot(root)
        assert len(warnings) == 1 and '使用最近成功版本' in warnings[0] and last_good['version'] == five['version']
        assert (root/'rules.json').read_bytes() == snapshot_bytes
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

    role_key = 'guide/Ellen Joe/subs'
    overrides = {role_key:dict(mode='only', values=['crit_', 'atk_'])}
    def role_change(previous, raw):
        return sub.merge(previous, {role_key:[raw]}, overrides, {})
    first = role_change({}, 'CR% > ATK%')
    reworded = role_change(first, 'ATK% = CRIT Rate')
    assert not sub.current_conflicts(reworded, overrides)
    changed = role_change(reworded, 'CR% > CD%')
    detail = sub.current_conflicts(changed, overrides)[role_key]
    assert sub.can_merge(detail) and detail['active'] == ['atk_', 'crit_'] and detail['remote'] == ['CR% > CD%']
    restored = role_change(changed, 'CR% / ATK%')
    assert not sub.current_conflicts(restored, overrides)
    only_removed = role_change(restored, 'CRIT Rate')
    assert not sub.can_merge(sub.current_conflicts(only_removed, overrides)[role_key])

    from drive_disc_manager.roster import compile_roster
    agent = dict(label='演示角色', selected=True, builds=['Example'], guides=[])
    sets = {'build/Example/sets2':['SwingJazz'], 'build/Example/sets4':['PolarMetal']}
    disabled = {k:dict(mode='only', values=[]) for k in sets}
    assert not compile_roster([agent], sets, disabled)[1]
    assert compile_roster([agent], {}, {})[1] == ['演示角色']

    # A new source term protects only the matching role/set/main while ranking spares.
    fields = {**sets, 'build/Example/slot4':['CR%'], 'build/Example/subs':['ATK% > New Stat']}
    rules = dict(fields=fields, version='new-term')
    cfg = dict(cfg, futureAgents=['example'], trimRecommended=True, reserve=0)
    relevant = plan(data, cfg, rules, {})['discs'][0]
    assert relevant['decision'] == '待复核' and any('角色副词条待核' in s and 'New Stat' in s for s in relevant['review'])
    assert any(k=='build/Example/subs' and 'New Stat' in s for k, _, s in relevant['ruleUses'])
    cfg['overrides'] = {'build/Example/subs':dict(mode='only',values=['atk_'])}
    assert plan(data, cfg, rules, {})['discs'][0]['decision'] == '弃置候选'
    unrelated = dict(data, discs=[dict(sample, setKey='Other')])
    assert all('New Stat' not in s for s in plan(unrelated, dict(cfg, overrides={}), rules, {})['discs'][0]['review'])

    # A disc-only scan can identify a character by the verified worn portrait.
    from drive_disc_manager.store import observed_agents, import_inventory
    only_gear = dict(data, discs=[dict(sample, ownerState='worn', location='Example')])
    assert observed_agents(only_gear) == {'Example'}
    assert not observed_agents(dict(data, discs=[dict(sample, ownerState='unknown', location='Example')]))
    cfg = dict(cfg, futureAgents=[], overrides={})
    assert plan(only_gear,cfg,rules,{})['roster'][0]['owned']
    assert not plan(only_gear,dict(cfg, excludedAgents=['example']),rules,{})['roster']
    tmp_scan = root/'disc-only.json'
    save(tmp_scan, only_gear); import_inventory(root, tmp_scan)
    save(tmp_scan, data); import_inventory(root, tmp_scan)
    assert read(root/'owned-agents.json') == ['Example']


if __name__ == '__main__':
    with TemporaryDirectory() as temp:
        guide_smoke(Path(temp))
    with TemporaryDirectory() as temp:
        smoke(Path(temp))
    print('PASS subscription: three-way decisions, stale edits, acknowledgement, union and failed schema')
