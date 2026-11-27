"""Resume one interrupted trial through the normal daily entry, with two discs and no game input."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import sys


def smoke(directory, *, incremental, unchanged=False):
    from one_dragon.utils import os_utils
    from drive_disc_manager.cn_export import normalize_disc, refresh
    from drive_disc_manager.incremental_marks import checkpoint
    from drive_disc_manager.inventory_comparison import verify_cn_inventory
    from drive_disc_manager.live import Marker
    from drive_disc_manager.manager_app import ManagerApp, wiki_path
    from drive_disc_manager.marks import owns_spare
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import config, fingerprint, read, save
    from drive_disc_manager.upgrade import RECEIPT

    root = directory/'1'
    os_utils.set_work_dir(str(directory))
    cfg = dict(config(root), inventorySource='cn_export' if incremental else 'ocr', auto_sync=False,
               autoUpgrade=not unchanged, applyMarkers=True, protectNaturalCrit=False, includeOwned=False,
               reserve=1, trimRecommended=True)
    save(root/'config.json', cfg)
    rules = dict(version='sample', fields={'matrix/TestSet/4':['atk_']})
    save(root/'rules.json', rules)
    path = root/'exports/zzz-drives-zood-v1.json'
    def raw(uid, level, stats):
        return dict(id=uid, setKey='TestSet', slotKey='4', mainStatKey='atk_', rarity='S', level=level,
                    substats=[dict(key=k, upgrades=n) for k,n in stats.items()], lock=True, trash=False, location='')
    def physical(d):
        return dict(normalize_disc(d), lockState='locked' if d['lock'] else 'unlocked', ownerState='bag', markState='unmarked', trash=False,
                    observedGrade='S')
    def stock(discs):
        return dict(format='ZOD', version=1, dbVersion=2, source='Zenless Optimizer', discs=discs,
                    characters=[], wengines=[], audit=dict(game_total=len(discs),exported=len(discs),delta=0))
    def export(discs):
        save(path, dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=discs))

    base = {'crit_':1,'hp':1,'def':1}
    old = raw('11',0,base)
    fourth = 'def_' if incremental else 'crit_dmg_'
    new = old if unchanged else raw('11',3,{**base, fourth:1})
    spare = dict(raw('22',15,{'crit_':3,'crit_dmg_':3,'hp':1,'def':1}), lock=False)
    before, after = physical(old), physical(new)
    rows = [before, physical(spare)]
    # The plugin locked this piece for trial in the preceding run.
    uid = old['id'] if incremental else None
    saved = dict(state='locked', spareLock=dict(origin='verified-spare-lock-v1', values=before['observedValues']))
    if uid:
        saved['uid'] = uid
    save(root/'markers.json', {fingerprint(before):saved})
    view = stock(rows)
    if incremental:
        export([old, spare]); refresh(root, lambda:None)
        net = read(root/'cn-inventory.json')
        view = verify_cn_inventory(net, view, rows, {})
    save(root/'inventory.json', view)
    planned = preview(root, wiki_path(), refresh=False, triage_only=True)
    if incremental:
        local = preview(root,wiki_path(),refresh=False,verified_game=True,cn_plan=planned,triage_only=True)
        checkpoint(root,net,planned,view,local,Obj(observed={},confirmed_locks={}))
    else:
        local = planned
    item = next(r for r in local['discs'] if r['id']=='11')
    save(root/RECEIPT, dict(status='pending', before=before, uid=uid, batch='previous', target=3, label='测试套装 4号盘', row=item,
                           limits=cfg, names={}, previousMatches=[]))
    readings = [after, physical(spare)]
    scanned = stock(readings)
    if not incremental:
        # The native task ran after the interruption. Its empty selection can be audited once the +3 is accounted for.
        from time import time
        from drive_disc_manager.salvage_audit import prepare_batch
        previous = prepare_batch(view, local, {}, 'A及以下', existing_only=True)
        previous.update(batch='previous', account=1, started=time()-10, nativeDay='day')
        save(root/'dismantle-preview.json', previous)
        path_record = directory/'config/01/app_run_record/drive_disc_dismantle.yml'
        path_record.parent.mkdir(parents=True)
        path_record.write_text(f'run_time_float: {time()-5}\nrun_status: 1\ndt: day\n', encoding='utf-8')
    scanner = Obj(config=Obj(scan_drive_disk=True), drive_disk_scanner=Obj(parser=None),
                  ocr_worker=Obj(scanned_discs=readings), _open_export=None)
    def scan():
        assert not incremental
        save(directory/'.debug/inventory_exports/inventory_recovery.json',scanned)
        return Obj(success=True)
    scanner.execute = scan
    app = Obj(root=root,account=1,group='one_dragon',ctx=Obj(current_instance_idx=1,
              controller=Obj(game_win=Obj(is_win_active=True)),
              run_context=Obj(is_context_running=True,get_application=lambda *_:scanner)),
              round_success=lambda msg:msg, round_fail=lambda msg:(_ for _ in ()).throw(AssertionError(msg)))
    seen = []
    def observations(_scanner,total,guard,*,by_acquisition):
        assert total == 2 and by_acquisition
        for d in readings:
            guard(); seen.append(d['id']); yield d, lambda:d
    def mark(marker):
        row = marker.rows[fingerprint(after)]
        assert row['managedSpareLock'] and '游戏内已锁定' not in row['reasons'], row
        assert owns_spare(read(root/'markers.json')[row['fp']],after['observedValues'],uid=uid)
        if incremental and not unchanged:
            assert row['decision'] == '弃置候选' and row['fp'] in marker.phase_targets('release'), row
            assert set(marker.phase_targets('mark')) == {row['fp']}
        else:
            assert row['decision'] == '保留' and not marker.phase_targets('mark')
        return marker.observed
    if incremental:
        export([new, spare])
    with ExitStack() as stack:
        for name, value in [('scanner_status.scanner_status',lambda *_:(True,'')),
                            ('dismantle.following_native',lambda *_:None), ('task_order.following_native',lambda *_:None),
                            ('scan_marks.capture_marks',lambda *_:nullcontext()), ('equip_targets.warehouse_observations',observations)]:
            stack.enter_context(patch('drive_disc_manager.'+name, value))
        marker = stack.enter_context(patch.object(Marker,'run',autospec=True,side_effect=mark))
        spend = stack.enter_context(patch('drive_disc_manager.upgrade_ui.UpgradeUI'))
        msg = ManagerApp.manage_inventory(app)
        marker.assert_called_once(); spend.assert_not_called()
    receipt = read(root/RECEIPT)
    assert receipt['status'] == ('unchanged' if unchanged else 'verified'), receipt
    expected = '仍是原等级' if unchanged else ('新增防御力 4.8%' if incremental else '新增暴击伤害 4.8%')
    assert expected in msg, msg
    if incremental:
        assert '11' in seen
    else:
        audit = read(root/'dismantle-result.json')
        assert audit['status'] == 'reconciled' and not audit['stockChanges']
        assert audit['beforeCounts'] == {fingerprint(d):1 for d in readings}
    print('PASS', 'CN delta' if incremental else 'OCR', 'unchanged' if unchanged else '+3 recovered before marking')


if __name__ == '__main__':
    home = Path(sys.argv[1]); sys.path[:0] = [str(Path(__file__).resolve().parents[1]),str(home/'src'),str(home/'plugins')]
    with TemporaryDirectory() as tmp:
        for incremental, unchanged in [(False,False),(True,False),(True,True)]:
            smoke(Path(tmp)/f'{incremental}-{unchanged}',incremental=incremental,unchanged=unchanged)
