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
    spare=piece('2',15,{'crit_':3,'crit_dmg_':3,'hp':1,'def':1}); snap=stock([before,spare])
    report=plan(snap,cfg,{'version':'v','fields':{'matrix/TestSet/4':['atk_']}},{})
    assert report['configuration']==digest(cfg) and eligible(report['discs'][0])
    def observe(*_,**__): yield before,lambda:before
    marker=Mock(); marker.detail.return_value=None
    with (patch('drive_disc_manager.equip_targets.warehouse_observations', observe),
          patch('drive_disc_manager.disc_probe.read_selected',return_value=before),
          patch('drive_disc_manager.live.Marker',return_value=marker),
          patch('drive_disc_manager.upgrade_ui.UpgradeUI') as adapter):
        ui=adapter.return_value; ui.preview.return_value=(0,6)
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
        assert '建议再试到 +6' in recover(root,pending,stock([good,spare]))
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
        ui.preview.side_effect = lambda:(current[0]['level'], current[0]['level']+3)
        ui.read_after.side_effect = read_after
        note = run_trials(app,None,report,snap,cfg,{},network_plan=remote)
        assert trace == [('1',0,1),('2',0,2),('1',3,2)], trace
        assert '2 张盘，共 3 档' in note and ui.confirm.call_count == 3
        batch = read(root/'dismantle-preview.json')
        assert batch['beforeCounts'] == {fingerprint(d):1 for d in bag.values()}
        assert batch['beforeUids'] == {k:uid_item(d) for k,d in bag.items()} and len(batch['upgradeMoves'])==3
        last = read(root/RECEIPT); upgrade.finalize(root, last, last['after'])
        assert read(root/'dismantle-preview.json') == batch
    print('PASS two trials, updated spare floors, three-tier budget, hand lock and replayable UID ledger')
