"""One trial disc against one spare. No game or material spending."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(sys.argv[1])/'src')]

from drive_disc_manager.cn_export import normalize_disc
from drive_disc_manager.planner import plan
from drive_disc_manager.store import config, digest, fingerprint, read, save
from drive_disc_manager.upgrade import RECEIPT, eligible, recover, run_trials


def piece(uid, level, stats):
    raw = dict(id=uid, rarity='S', level=level, setKey='TestSet', slotKey='4', mainStatKey='atk_',
               substats=[dict(key=k, upgrades=n) for k,n in stats.items()], lock=False, location='', trash=False)
    return dict(normalize_disc(raw), ownerState='bag', lockState='unlocked', markState='unmarked', trash=False, observedGrade='S')


def stock(discs):
    return dict(format='ZOD', source='Zenless Optimizer', dbVersion=2, version=1, characters=[], wengines=[],
                discs=discs, audit=dict(exported=len(discs), game_total=len(discs), delta=0))


with TemporaryDirectory() as tmp:
    root=Path(tmp)/'1'; cfg=dict(config(root), autoUpgrade=True, applyMarkers=True, includeOwned=False, reserve=1)
    save(root/'config.json',cfg)
    app=SimpleNamespace(root=root, account=1,ctx=SimpleNamespace(current_instance_idx=1,
                        controller=SimpleNamespace(game_win=SimpleNamespace(is_win_active=True)),
                        run_context=SimpleNamespace(is_context_running=True)))
    base={'crit_':1,'def':1,'hp':1}; before=piece('1',0,base)
    from drive_disc_manager.potential import verdict
    only_crit = dict(label='偏暴击', stats=['crit_'], minimum=6)
    assert '最多 5 档' in verdict(0, base, [only_crit])
    assert not verdict(0, base, [dict(only_crit, minimum=5)])
    assert not verdict(0, base, [dict(only_crit, stats=['crit_', 'crit_dmg_'])])
    spare=piece('2',15,{'crit_':3,'crit_dmg_':3,'hp':1,'def':1}); snap=stock([before,spare])
    # Reducing two slots to one keeps a trial that can still tie the six-roll spare.
    from drive_disc_manager.reserve_policy import quota_impact
    rules = {'version':'v','fields':{'matrix/TestSet/4':['atk_']}}
    two_slots = dict(cfg, reserve=2, protect15=False, trimRecommended=True)
    one_slot = dict(two_slots, reserve=1)
    original = plan(snap, two_slots, rules, {})
    impact = quota_impact(original, {}, one_slot, {})
    assert impact['after'] == plan(snap, one_slot, rules, {})['counts']['保留'] == 2
    assert impact['spares'] == impact['potential'] == 1 and not impact['newCandidates']
    # This +3 has one useful roll and four upgrades left. The +15 already has six useful rolls.
    weak = piece('1', 3, dict(base, def_=1))
    tested = stock([weak, spare])
    protected_spare = dict(one_slot, protect15=True)
    report_after = plan(tested, protected_spare, rules, {})
    assert report_after['discs'][0]['decision'] == '弃置候选'
    assert report_after['reserveCoverage'][0]['kept'] == report_after['reserveCoverage'][0]['purposes'][0]['covered'] == 0
    same = quota_impact(report_after, {}, protected_spare, {})
    assert same['before'] == same['after'] == 1 and same['spares'] == same['potential'] == 0
    # The player's own protected disc stays outside the extra spare quota.
    just_crit = [dict(purpose='通用', count=1, stats=['crit_'])]
    competing = piece('2', 15, {'crit_':6, 'hp':1, 'def':1, 'hp_':1})
    floor_cfg = dict(cfg, trimRecommended=True, reserve_rules=just_crit, pins=[fingerprint(competing)])
    for other in (competing, dict(competing, lock=True, lockState='locked')):
        local_cfg = dict(floor_cfg, pins=[] if other['lock'] else floor_cfg['pins'])
        small = stock([before, other]); match_rules = {'version':'v','fields':{'matrix/TestSet/4':['atk_']}}
        pick, protected = plan(small,local_cfg,match_rules,{})['discs']
        assert protected['decision'] == '保留' and pick['decision'] == '保留', (pick['reasons'],pick['explanation'])
        assert pick['upgradeFloor'][0]['minimum'] == 0
    report=plan(snap,cfg,{'version':'v','fields':{'matrix/TestSet/4':['atk_']}},{})
    assert report['configuration']==digest(cfg) and eligible(report['discs'][0])
    def observe(*_,**__): yield before,lambda:before
    marker=Mock(); marker.detail.return_value=None
    with (patch('drive_disc_manager.equip_targets.warehouse_observations', observe),
          patch('drive_disc_manager.disc_probe.read_selected',return_value=before),
          patch('drive_disc_manager.live.Marker',return_value=marker),
          patch('drive_disc_manager.upgrade_ui.UpgradeUI') as adapter):
        ui=adapter.return_value; ui.preview.return_value=(0,6)
        ui.confirm.side_effect = lambda current, goal, before_click: before_click()
        try: run_trials(app,None,report,snap,cfg,{})
        except ValueError as exc: assert '+6' in str(exc)
        else: raise AssertionError('accepted a second tier')
        ui.confirm.assert_not_called()
        ui.preview.return_value=(0,3); ui.read_after.side_effect=ValueError('interrupted')
        try: run_trials(app,None,report,snap,cfg,{})
        except ValueError as exc: assert 'interrupted' in str(exc)
        else: raise AssertionError('unexpected success')
        pending=read(root/RECEIPT); assert pending['status']=='pending'
        good=piece('1',3,dict(base,crit_dmg_=1)); bad=piece('1',3,dict(base,def_=1))
        note = recover(root,pending,stock([good,spare]))
        assert '新增暴击伤害 4.8%' in note and '建议再试到 +6' in note
        # A character likes both crit stats; the player's only active use counts crit rate alone.
        from drive_disc_manager.upgrade import after_advice, finalize
        row = dict(pending['row'], roleStats={'主C':['crit_', 'crit_dmg_']})
        assert after_advice(row,good,cfg,{})['target'] == 6
        assert after_advice(row,good,dict(cfg,reserve=0),{}) is None
        assert after_advice(row,good,dict(cfg,reserve_rules=just_crit),{}) is None
        crit_damage = dict(cfg,reserve=0,reserve_rules=[dict(purpose='偏暴伤', count=1)])
        assert after_advice(row,piece('1',6,dict(base,crit_dmg_=2)),crit_damage,{}) is None
        # Returning to the warehouse can select an older +3 that looks like a possible result.
        # Start at the real spend entry so the prior lines and copy counts come from the bag.
        from drive_disc_manager.upgrade import execute
        old_copy = dict(good, id='9', setKey='测试盘')
        alias = {'TestSet':'测试盘'}
        trial_ui = Mock()
        trial_ui.preview.return_value = (0,3)
        trial_ui.confirm.side_effect = lambda current, goal, before_click: before_click()
        trial_ui.read_after.return_value = good
        batch = dict(batch='copy', beforeCounts={fingerprint(d):1 for d in (before,old_copy)}, entries=[])
        save(root/'dismantle-preview.json', batch)
        history = read(root/'markers.json',{})
        try:
            execute(root, trial_ui, pending['row'], before, stock([before,old_copy]), cfg, alias, 'copy')
        except ValueError as exc:
            assert '等下次整理核对' in str(exc), exc
        else:
            raise AssertionError('accepted a pre-existing +3 as the result')
        duplicate = read(root/RECEIPT)
        assert duplicate['status'] == 'pending' and 'after' not in duplicate
        assert read(root/'dismantle-preview.json') == batch and read(root/'markers.json',{}) == history
        assert '仍是原等级' in recover(root,duplicate,stock([before,old_copy]))
        assert read(root/'dismantle-preview.json') == batch
        interrupted = {k:v for k,v in duplicate.items() if k not in ('observedCopy', 'note')}
        assert '停在 +3' in recover(root,dict(interrupted, batch=None),stock([old_copy,good]))
        for changed in ([old_copy,good,spare], [bad,old_copy,spare]):
            try: recover(root,interrupted,stock(changed))
            except ValueError: pass
            else: raise AssertionError('attributed a tier despite other unexplained same-family changes')
        # The actual trial may have gained a different fourth stat.
        try:
            recover(root,duplicate,stock([bad,old_copy,spare]))
        except ValueError as exc:
            assert '待核对' in str(exc), exc
        else:
            raise AssertionError('attributed a lookalike despite a second change to the bag')
        assert '新增防御力' in recover(root,duplicate,stock([bad,old_copy]))
        save(root/'dismantle-preview.json', batch)
        unrelated = dict(spare, setKey='AnotherSet')
        assert '停在 +3' in recover(root,duplicate,stock([old_copy,good,unrelated]))
        assert read(root/RECEIPT)['nextUpgrade'] is None and not read(root/RECEIPT)['outgrown']
        assert read(root/'dismantle-preview.json')['upgradeMoves'] == {fingerprint(before):fingerprint(good)}
        # With a bound UID a second, unrelated drop does not obscure the target's level.
        bound_disc = dict(good, exportUid=good['id'], ownerState='unknown', markState='unknown', observedValues=None)
        bound = dict(stock([bound_disc,old_copy,spare]), source='OhMyZZZ CN', exportFile='account-1.json', exportWrittenNs=2)
        bound['exportWrittenNs'] = duplicate['submittedNs'] + 2
        cn_receipt = dict(duplicate, uid=good['id'], batch=None, exportFile=bound['exportFile'], exportWrittenNs=1)
        stale_before = dict(bound, discs=[dict(before, exportUid=before['id']),old_copy,spare], exportWrittenNs=1)
        for other in (dict(bound, exportFile='other-account.json'), dict(bound, exportWrittenNs=1), stale_before):
            try: recover(root, cn_receipt, other)
            except ValueError: pass
            else: raise AssertionError('used an unrelated or stale export to settle the trial')
        assert '停在 +3' in recover(root, cn_receipt, bound)
        blind = {k:v for k,v in cn_receipt.items() if k != 'observedCopy'}
        for wrong in (stale_before, dict(stale_before, exportWrittenNs=blind['submittedNs']),
                      dict(stale_before, exportWrittenNs=bound['exportWrittenNs'], exportFile='unrelated.json')):
            try: recover(root,blind,wrong)
            except ValueError as exc: assert '国服仓库导出更新' in str(exc)
            else: raise AssertionError('closed a submitted tier using an export from before the click or another file')
        assert trial_ui.confirm.call_count == 1
        print('PASS old +3 in warehouse, unchanged level, different fourth stat, exact copy count and bound UID')
        assert ui.confirm.call_count==1
        ui.read_after.side_effect=None;ui.read_after.return_value=bad
        marker.detail.return_value=(fingerprint(bad),'unlocked','unmarked',False)
        def mark(mode,detail,row):
            assert mode=='mark' and row['decision']=='弃置候选' and row['fp']==fingerprint(bad)
            marker.detail.return_value=(fingerprint(bad),'unlocked','marked',False)
            return True
        marker.handle_disc.side_effect=mark
        assert '已标记弃置' in run_trials(app,None,report,snap,cfg,{})
        assert marker.handle_disc.call_count==1 and read(root/RECEIPT)['marked']
    print('PASS +3 vs +6, confirmation recovery, useful fourth line, outgrown spare marked')

    # Run the lock and trial stages against the same two physical pieces.
    from drive_disc_manager.live import Marker
    from drive_disc_manager.marks import owns_spare
    from drive_disc_manager.disc_trace import probe_trace
    natural = piece('11', 0, dict(base, crit_dmg_=1))
    hand = dict(piece('12', 0, {'crit_':1, 'crit_dmg_':1, 'def_':1, 'hp_':1}), lock=True, lockState='locked')
    protected = stock([natural, hand]); report = plan(protected,cfg,rules,{})
    row, personal = report['discs']
    assert eligible(row) and not eligible(personal) and '四词条双暴胚子' in row['reasons']
    detail_row, _ = probe_trace(dict(status='verified', disc=dict(natural, fingerprint=row['fp'])),cfg,rules,{},())
    assert any('建议试到 +3' in note for note in detail_row['explanation'])
    game_disc = dict(natural)
    game = Marker.__new__(Marker); game.root=root; game.report=report; game.cfg=cfg; game.app=app
    game.history={}; game.observed={}; game.confirmed_locks={}; game.rows={r['fp']:r for r in report['discs']}
    game.detail = lambda:(row['fp'], game_disc['lockState'], 'unmarked', False)
    def game_click(point):
        assert point.x == 1530 and not game_disc['lock']
        game_disc.update(lock=True,lockState='locked')
    app.ctx.controller.click = game_click
    with patch('drive_disc_manager.live.time.sleep'):
        assert game.handle_disc('lock',game.detail(),row)
    assert owns_spare(read(root/'markers.json')[row['fp']], natural['observedValues'])
    def pages(*args, by_acquisition):
        assert by_acquisition
        for disc in (hand, game_disc):
            yield disc, lambda:disc
    grown = dict(piece('11',3,dict(base,crit_dmg_=2)), lockState='locked', lock=True)
    with (patch('drive_disc_manager.equip_targets.warehouse_observations',pages),
          patch('drive_disc_manager.disc_probe.read_selected',side_effect=lambda *_:dict(game_disc)),
          patch('drive_disc_manager.upgrade_ui.UpgradeUI') as adapter):
        # A player removes the just-applied lock before the trial. The old scan still says unlocked.
        game_disc.update(lock=False,lockState='unlocked')
        assert '没有找到' in run_trials(app,None,report,protected,cfg,{})
        adapter.assert_not_called()
        # Replay the uninterrupted path from immediately after the confirmed plugin lock.
        game_disc.update(lock=True,lockState='locked')
        ui=adapter.return_value; ui.preview.return_value=(0,3); ui.read_after.return_value=grown
        ui.confirm.side_effect = lambda current, goal, before_click: before_click()
        note = run_trials(app,None,report,protected,cfg,{})
        assert ui.confirm.call_count == 1 and ui.confirm.call_args.args == (0,3)
        assert '暴击伤害 4.8% → 9.6%' in note and owns_spare(read(root/'markers.json')[fingerprint(grown)],grown['observedValues'])
    print('PASS newly locked natural double crit, manual lock and manual unlock, +3 with lock retained')

    # Two +0 drops compete for one spare slot. The second comparison must see the first +3.
    from drive_disc_manager import upgrade
    from drive_disc_manager.salvage_audit import uid_item
    rules = {'version':'v','fields':{'matrix/TestSet/4':['atk_']}}
    cfg.update(upgradeSteps=3, trimRecommended=True, inventorySource='cn_export')
    save(root/'config.json', cfg)
    one = piece('1', 0, base)
    two = piece('2', 0, {'crit_dmg_':1,'hp_':1,'def':1})
    spare = piece('3', 15, {'crit_':1,'hp_':3,'def':3,'hp':1})
    locked = dict(piece('4',0,{'crit_':1,'def':1,'hp_':1}), lock=True, lockState='locked')
    bag = {d['id']:dict(d, exportUid=d['id']) for d in (locked, one, two, spare)}
    snap = dict(stock(list(bag.values())), source='OhMyZZZ CN')
    net = dict(snap, source='zzz-drive-exporter', networkPreview=True,
               discs=[dict(d, ownerState='unknown', markState='unknown') for d in bag.values()])
    save(root/'cn-inventory.json', net)
    remote = plan(net, cfg, rules, {}); report = plan(snap,cfg,rules,{})
    assert not eligible(report['discs'][0])
    assert all(eligible(r) for r in report['discs'][1:3])
    save(root/'dismantle-preview.json', dict(batch='b', inventory=report['inventory'],account=1, entries=[],
         beforeCounts={fingerprint(d):1 for d in bag.values()},beforeUids={k:uid_item(d) for k,d in bag.items()}))
    current = []; trace = []
    def observe(*_, **__):
        for d in bag.values():
            current[:] = [d]
            yield d, lambda:d
    def read_after():
        old = current[0]; stats = upgrade.rolls(old); uid = old['id']
        key = 'crit_dmg_' if uid == '1' else 'crit_'
        stats[key] = stats.get(key,0) + 1
        d = dict(piece(uid, old['level']+3, stats), exportUid=uid)
        bag[uid] = d
        trace.append((uid, old['level'], read(root/RECEIPT)['row']['upgradeFloor'][0]['minimum']))
        return d
    with (patch('drive_disc_manager.equip_targets.warehouse_observations', observe),
          patch('drive_disc_manager.disc_probe.read_selected', side_effect=lambda *_:current[0]),
          patch('drive_disc_manager.upgrade_ui.UpgradeUI') as adapter):
        ui = adapter.return_value
        ui.confirm.side_effect = lambda current, goal, before_click: before_click()
        ui.preview.side_effect = lambda:(current[0]['level'], current[0]['level']+3)
        ui.read_after.side_effect = read_after
        note = run_trials(app,None,report,snap,cfg,{},network_plan=remote)
        assert trace == [('1',0,1),('2',0,2),('1',3,2)], trace
        assert '2 张盘，共 3 档' in note and ui.confirm.call_count == 3
        batch = read(root/'dismantle-preview.json')
        assert batch['beforeCounts'] == {fingerprint(d):1 for d in bag.values()}
        assert batch['beforeUids'] == {k:uid_item(d) for k,d in bag.items()} and len(batch['upgradeMoves'])==3
        last = read(root/RECEIPT); upgrade.finalize(root, last, last['after'])
        from drive_disc_manager.trial_history import latest_trials
        history = latest_trials(root)
        assert [(step['before'], step['level']) for step in history] == [(0,3), (0,3), (3,6)]
        assert [step['change'] for step in history] == ['新增暴击伤害 4.8%', '新增暴击率 2.4%', '暴击伤害 4.8% → 9.6%']
        assert '暴击伤害 4.8% → 9.6%' in last['note']
        assert read(root/'dismantle-preview.json') == batch
    print('PASS two trials, updated spare floors, three-tier budget, hand lock and replayable UID ledger')

    # A copy of a three-line +0 observed on CN. Other pieces and quotas are fixtures.
    from drive_disc_manager.potential import saved_comparison
    from drive_disc_manager.disc_trace import probe_trace
    fresh = piece('31', 0, {'def':1, 'def_':1, 'anomProf':1})
    expert = piece('32', 15, {'anomProf':6, 'crit_':1, 'hp':1, 'def':1})
    snapshot = stock([fresh, expert])
    trial_cfg = dict(cfg, reserve=0, autoUpgrade=True, inventorySource='ocr', trimRecommended=False,
                     reserve_rules=[dict(purpose='异常精通', stats=['anomProf'], count=1),
                                    dict(purpose='偏暴击', count=None)])
    trial_report = plan(snapshot, trial_cfg, rules, {})
    item = trial_report['discs'][0]
    assert item['decision'] == '保留' and not item.get('upgrade')
    read_only = plan(snapshot, dict(trial_cfg, autoUpgrade=False), rules, {})['discs'][0]
    assert read_only['decision'] == '保留' and not read_only.get('upgrade')
    save(root/'config.json', trial_cfg)
    probe = dict(status='verified', disc=dict(fresh, fingerprint=fingerprint(fresh)))
    result = run_trials(app, None, trial_report, snapshot, trial_cfg, {})
    assert result == '没有适合试强化的盘'
    # At +9 three useful rolls would normally qualify, but only two upgrades remain.
    developed = piece('31', 9, {'def':1, 'def_':1, 'anomProf':3, 'hp':2})
    assert after_advice(dict(item, upgradeFloor=[]), developed, trial_cfg, {})['target'] == 12
    assert after_advice(item, developed, trial_cfg, {}) is None
    save(root/'inventory.json', snapshot)
    save(root/'preview.json', dict(trial_report, generated=1_795_780_000))
    comparison = saved_comparison(root, trial_cfg, rules)
    notes = probe_trace(probe, trial_cfg, rules, {}, (), comparison=comparison)[0]['explanation']
    assert any('暂缓强化' in note for note in notes) and not any('建议试到' in note for note in notes)
    assert saved_comparison(root, trial_cfg, dict(rules, version='next')) is None
    assert saved_comparison(root, dict(trial_cfg, reserve=1), rules) is None
    tied = piece('32', 15, {'anomProf':5, 'crit_':1, 'hp':2, 'def':1})
    save(root/'inventory.json', stock([fresh, tied]))
    assert saved_comparison(root, trial_cfg, rules) is None
    picked = plan(stock([fresh, tied]), trial_cfg, rules, {})['discs'][0]
    assert picked['upgrade']['purpose'] == '异常精通' and picked['upgrade']['target'] == 3
    print('PASS split-purpose thresholds, retained without a spend, tied spare, stale comparison')

    # Same three raw substats as the saved 4-slot Dawn's Bloom. Compare
    # two possible fourth lines against one six-roll proficiency spare.
    fresh = dict(fresh, setKey="Dawn'sBloom")
    expert = dict(expert, setKey="Dawn'sBloom")
    rules = dict(version='sample', fields={"matrix/Dawn'sBloom/4":['atk_']})
    just_trials = dict(trial_cfg, trimRecommended=True,
                       reserve_rules=[dict(purpose='异常输出', stats=['anomProf', 'pen'], count=1),
                                      dict(purpose='偏暴击', count=None)])
    def test_fourth(stat):
        after = dict(piece('31', 3, {'def':1, 'def_':1, 'anomProf':1, stat:1}), setKey=fresh['setKey'])
        trial_row = plan(stock([fresh, expert]), just_trials, rules, {})['discs'][0]
        assert eligible(trial_row) and trial_row['upgrade']['target'] == 3
        receipt = dict(before=fresh, row=trial_row, limits=just_trials, names={}, label='拂晓生花 4号盘', previousMatches=[])
        finalize(root, receipt, after)
        result = read(root/RECEIPT)
        replanned = plan(stock([after,expert]), just_trials, rules, {})['discs'][0]
        return result, replanned
    missed, row = test_fourth('hp')
    assert missed['outgrown'] and '最多 5 档' in missed['note'] and row['decision'] == '弃置候选', missed['note']
    hit, row = test_fourth('crit_')
    assert not hit['outgrown'] and not hit['nextUpgrade'] and row['decision'] == '保留', hit['note']
    # A sole, unlimited crit quota still needs crit once all four lines are known.
    crit_only = dict(just_trials, reserve_rules=[dict(purpose='偏暴击', count=None)])
    miss = missed['after']
    only = plan(stock([miss]), crit_only, rules, {})['discs'][0]
    assert only['decision'] == '弃置候选' and '出不了' in only['explanation'][-1]
    general = plan(stock([miss]), dict(crit_only, reserve=1), rules, {})['discs'][0]
    assert general['decision'] == '保留' and '备用 · 通用' in general['reasons']
    print('PASS +3 without crit misses both spare uses; +3 with crit stays as a spare')
