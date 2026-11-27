"""Three-disc smoke for replacing a spare with no game, account or network access."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from drive_disc_manager.live import Marker
    from drive_disc_manager.marks import clear_manual_exception, owns_spare, reconcile_scan
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import defaults, digest, fingerprint, read, save

    def disc(number, subs, locked=False):
        return dict(id=number, setKey='SwingJazz', slotKey='4', level=0, rarity='S', mainStatKey='def_',
                    substats=[dict(key=k, upgrades=0) for k in subs], observedValues=dict(main={'def_':12}, subs=subs),
                    markState='unmarked', trash=False, lockState='locked' if locked else 'unlocked', lock=locked)

    old = disc('1', {'crit_':2.4, 'hp':112, 'atk':19})
    fresh = disc('2', {'crit_':2.4, 'crit_dmg_':4.8, 'atk_':3})
    manual = disc('3', {'pen':9, 'hp':112, 'atk':19}, locked=True)
    old_id, new_id, manual_id = map(fingerprint, (old, fresh, manual))
    cfg = dict(defaults(), reserve=1, trimRecommended=True, applyMarkers=True, run_scanner=True)
    names = {'SwingJazz':'摇摆爵士'}
    rules = dict(version='fixture', fields={'matrix/SwingJazz/4':['def_'], 'matrix/SwingJazz/4/known':['def_']})

    def inventory(*discs):
        return dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=list(discs),
                    audit=dict(game_total=len(discs), exported=len(discs), delta=0))

    with TemporaryDirectory() as directory, patch('drive_disc_manager.live.time.sleep'):
        root = Path(directory)/'1'
        history_path = root/'markers.json'
        clicked = []

        def scan(z):
            save(root/'inventory.json', z)
            reconcile_scan(root, z, names)
            save(root/'config.json', cfg)
            marker = Marker.__new__(Marker)
            marker.root, marker.cfg = root, cfg
            marker.history = read(history_path, {})
            marker.report = plan(z, cfg, rules, names, history=marker.history)
            marker.rows = {row['fp']:row for row in marker.report['discs']}
            marker.seen_counts = Counter(row['fp'] for row in marker.report['discs'])
            marker.observed, marker.released = {}, set()
            marker.confirmed_locks = {}
            return marker

        def visit(marker, mode, d):
            fp = fingerprint(d)
            marker.detail = lambda:(fp, d['lockState'], d['markState'], bool(d.get('location')))
            def click(point):
                assert read(history_path)[fp]['state'] == 'pending'
                if point.x == 1530:
                    d['lock'] = not d['lock']
                    d['lockState'] = 'locked' if d['lock'] else 'unlocked'
                else:
                    assert point.x == 1447 and not d['lock'] and not d['trash']
                    d.update(trash=True, markState='marked')
                clicked.append((d['id'], mode))
            marker.app = SimpleNamespace(ctx=SimpleNamespace(controller=SimpleNamespace(click=click)))
            assert marker.handle_disc(mode, marker.detail(), marker.rows[fp])

        m = scan(inventory(old, manual))
        assert m.rows[old_id]['reasons'] == ['备用 · 通用']
        visit(m, 'lock', old)
        assert owns_spare(m.history[old_id], old['observedValues'])
        original = deepcopy(m.history)
        m = scan(inventory(old, fresh, manual))
        assert m.rows[old_id]['reasons'] == ['备用 · 通用'] and m.rows[new_id]['decision'] == '弃置候选'
        group = m.report['reserveCoverage'][0]
        assert group['eligible'] == 2 and group['kept'] == group['purposes'][0]['covered'] == 1
        assert m.rows[manual_id]['reasons'] == ['游戏内已锁定']
        from drive_disc_manager.reserve_policy import quota_impact
        impact = quota_impact(m.report, {'set':'SwingJazz'}, dict(cfg,reserve=0), {})
        assert impact['after'] == 2 and impact['spares'] == 1 and impact['newCandidates'] == 0
        cfg['reserve'] = 0
        capped = scan(inventory(old, fresh, manual))
        zero = capped.report['reserveCoverage'][0]
        assert zero['kept'] == zero['fixedExtra'] == 1 and zero['purposes'][0]['covered'] == 0
        assert capped.rows[old_id]['decision'] == '保留'
        cfg['reserve'] = 1
        cfg['rotateSpareLocks'] = True
        tie = disc('tie', {'crit_':2.4, 'pen':9, 'def':15})
        # This pair reverses its raw fingerprint order when its set name is translated.
        def picked(rows):
            return [r['id'] for r in rows if r['decision'] == '保留']
        unlocked = dict(old, lock=False, lockState='unlocked')
        def preview_pair(pair): return plan(inventory(*pair), cfg, rules, names)['discs']
        base_pair = [unlocked, tie]
        assert picked(preview_pair(base_pair)) == picked(preview_pair([dict(d, setKey='摇摆爵士') for d in reversed(base_pair)]))
        equal = scan(inventory(tie, old))
        assert picked(equal.report['discs']) == [old['id']] and not equal.phase_targets('release')
        # A single double-crit disc can fill both extra purposes outside the team's gear.
        special = [dict(purpose='偏暴击', count=1), dict(purpose='偏暴伤', count=1)]
        multi_cfg = dict(cfg, reserve=0, reserve_rules=special)
        overlap = plan(inventory(fresh,dict(old,location='Nicole'),manual),multi_cfg,rules,names)
        uses = overlap['reserveCoverage'][0]
        assert uses['kept'] == uses['eligible'] == 1 and [p['covered'] for p in uses['purposes']] == [0,1,1]
        impact = quota_impact(overlap, {'set':'SwingJazz'}, multi_cfg, {})
        assert impact['byPurpose'] == {'偏暴击':1, '偏暴伤':1} and impact['spares'] == impact['shared'] == 1
        for name, excluded in [('偏暴击', 'crit_dmg_'), ('偏暴伤', 'crit_')]:
            other = disc('other', {excluded:4.8 if excluded == 'crit_dmg_' else 2.4, 'atk_':3, 'def':15})
            one = dict(multi_cfg, reserve_rules=[dict(purpose=name, count=1)])
            assert plan(inventory(other),one,rules,names)['discs'][0]['decision'] == '弃置候选'
            one['reserve_rules'][0]['stats'] = ['atk_']
            assert plan(inventory(other),one,rules,names)['discs'][0]['reasons'] == ['备用 · '+name]
        for extra, pins in [({'location':'TestAgent'}, []), ({}, [old_id])]:
            cfg['pins'] = pins
            m = scan(inventory(dict(old, **extra), fresh, manual))
            assert m.rows[old_id]['decision'] == '保留' and m.rows[old_id]['reasons']
        cfg['pins'] = []
        m = scan(inventory(old, fresh, manual))
        assert m.rows[old_id]['decision'] == '弃置候选' and m.rows[new_id]['reasons'] == ['备用 · 通用']
        assert m.rows[manual_id]['reasons'] == ['游戏内已锁定']
        original_report = deepcopy(m.report)
        impact = quota_impact(m.report, {'set':'SwingJazz'}, dict(cfg,reserve=0), {})
        assert impact == dict(total=3,before=2,after=1,newCandidates=1,spares=0,groups=1,byPurpose={},shared=0)
        assert m.report == original_report
        visit(m, 'lock', fresh)
        visit(m, 'lock', manual)
        visit(m, 'release', old)
        m.released.clear()
        try:
            visit(m, 'mark', old)
        except ValueError as exc:
            assert '本轮' in str(exc)
        else:
            raise AssertionError('stale release admitted')
        m.released.add(old_id)
        visit(m, 'mark', old)
        assert clicked == [('1','lock'), ('2','lock'), ('1','release'), ('1','mark')]
        m = scan(inventory(old, fresh, manual))
        assert m.rows[old_id]['decision'] == '已弃置'
        for d in (fresh, manual): visit(m, 'lock', d)
        assert len(clicked) == 4

        # User unlock, undo exception, collision, old-version lock and interrupted click.
        old.update(trash=False, markState='unmarked')
        save(history_path, original)
        z = inventory(old, fresh, manual)
        m = scan(z)
        assert m.rows[old_id]['manualUnlock'] and m.rows[old_id]['decision'] == '保留'
        visit(m, 'lock', old)
        assert not old['lock']
        clear_manual_exception(root, m.rows[old_id], digest(z), names)
        assert old_id not in read(history_path)
        old.update(lock=True, lockState='locked')
        for z in (inventory(old, dict(old, id='4', setKey='摇摆爵士')), inventory(fresh),
                  inventory(dict(old, observedValues=None))):
            save(history_path, original)
            reconcile_scan(root, z, names)
            assert 'spareLock' not in read(history_path)[old_id]
        save(history_path, {old_id:dict(state='locked')})
        assert scan(inventory(old)).rows[old_id]['reasons'] == ['游戏内已锁定']
        for state in ('released','pending'):
            save(history_path, {old_id:dict(state=state, intended='release', values=old['observedValues'])})
            reconcile_scan(root, inventory(dict(old, observedValues=None)), names)
            assert read(history_path)[old_id]['state'] == state
            m = scan(inventory(old))
            assert m.rows[old_id]['manualUnlock'] and not m.rows[old_id]['managedSpareLock']
        # The interrupted item might have disappeared in an independent native run.
        pending = {old_id: dict(state='pending', intended='mark', values=old['observedValues'])}
        save(history_path, pending)
        reconcile_scan(root, inventory(dict(fresh, observedValues=None)), names)
        assert read(history_path) == pending
        reconcile_scan(root, inventory(dict(old, setKey='摇摆爵士')), names)
        assert read(history_path) == pending
        reconcile_scan(root, inventory(fresh), names)
        retired = read(history_path)[old_id]
        assert retired['state']=='absent-after-rescan' and retired['intended']=='mark'
        assert retired['values']==old['observedValues'] and 'spareLock' not in retired
        assert len(clicked) == 4
        # A fresh scan containing only an already discarded piece has no reversible work.
        only_marked = dict(old, trash=True, markState='marked', lock=False,lockState='unlocked')
        m = scan(inventory(only_marked))
        m.app = SimpleNamespace(ctx=None, account=1, group='one_dragon')
        with patch('drive_disc_manager.live.marker_conflict',return_value=''), patch.object(m,'apply') as apply:
            assert m.run() == {} and m.report['discs'][0]['decision'] == '已弃置'
            apply.assert_not_called()
        unmarked = dict(only_marked, trash=False, markState='unmarked')
        z = inventory(unmarked, fresh, manual)
        m = scan(z)
        assert m.rows[old_id]['manualKeep'] and '手动撤销弃置后保留' in m.rows[old_id]['reasons']
        assert old_id not in cfg['pins']
        clear_manual_exception(root, m.rows[old_id], digest(z), names)
        assert scan(z).rows[old_id]['reasons'] == ['备用 · 通用']
        cfg['reserve'] = 0
        assert scan(z).rows[old_id]['decision'] == '弃置候选' and len(clicked) == 4
    print('PASS spare quotas across scans, purpose overlap, stable ties, rotation and manual protection')


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    smoke(args.parse_args().home.resolve())
