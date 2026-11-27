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
from drive_disc_manager.upgrade import RECEIPT, eligible, recover, try_one


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
        try: try_one(app,None,report,snap,cfg,{})
        except ValueError as exc: assert '+6' in str(exc)
        else: raise AssertionError('accepted a second tier')
        ui.confirm.assert_not_called()
        ui.preview.return_value=(0,3); ui.read_after.side_effect=ValueError('interrupted')
        try: try_one(app,None,report,snap,cfg,{})
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
            return True
        marker.handle_disc.side_effect=mark
        assert '已标记弃置' in try_one(app,None,report,snap,cfg,{})
        assert marker.handle_disc.call_count==1 and read(root/RECEIPT)['marked']
    print('PASS +3 vs +6, confirmation recovery, useful fourth line, outgrown spare marked')
