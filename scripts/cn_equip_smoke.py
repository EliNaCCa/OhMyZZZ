"""Replay two Soldier 11 builds with seven recorded discs and fixture CN IDs."""
import argparse
import sys
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch


def smoke(home, source, temp):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from one_dragon.utils import os_utils
    from drive_disc_manager.cn_export import EXPORT_NAME, FILE, refresh
    from drive_disc_manager.manual_equip import MANUAL_GROUP, confirmation
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.equip_review import changed_slots
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import config, equipment_names, equipment_keys, equipment_key, read, save

    root = temp/'accounts'/'1'
    os_utils.set_work_dir(str(temp))
    wiki_path = next(p for p in (home/'plugin_data/wiki_data/zzz_translation.json',
                                 home/'plugins/inventory_scan/assets/wiki_data/zzz_translation.json') if p.is_file())
    names = equipment_names(read(wiki_path))
    reverse = equipment_keys(names)
    data = read(source)['discs']
    worn = sorted((d for d in data if d.get('location') == 'Soldier 11'), key=lambda d: int(d['slotKey']))
    spare = next(d for d in data if d.get('ownerState') == 'bag' and d.get('level') == 15 and
                 d.get('slotKey') == '4' and d.get('markState') == 'unmarked' and d['rarity'] == 'S')
    assert len(worn) == 6 and all(d['markState'] == 'unmarked' for d in worn)
    readings = [*worn, spare]
    raw = [dict(d, id=str(i), setKey=equipment_key(d['setKey'], names, reverse), location='')
           for i, d in enumerate(readings, 1)]
    save(root/'exports'/EXPORT_NAME, dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=raw))
    profiles = [dict(name=name, request=dict(characterName='11号'), agentKey='Soldier 11') for name in ('日常','爆发')]
    cfg = dict(config(root), profiles=profiles, inventorySource='cn_export', auto_sync=False, protect15=False, reserve=0)
    save(root/'config.json', cfg)
    refresh(root, lambda: None)
    cn = read(root/FILE)

    def choose(_url, profile, pool, _current):
        ids = ['1', '2', '3', '4' if profile['name'] == '日常' else '7', '5', '6']
        assert set(ids) <= {d['id'] for d in pool}
        return [dict(ids=ids, outputScore=100, critRate=100)]

    with (patch('drive_disc_manager.engine.managed', return_value=nullcontext('fixture')),
          patch('drive_disc_manager.optimizer.candidate_pool', side_effect=choose)):
        planned = preview(root, wiki_path, refresh=False)
    assert all(r['decision'] == '保留' for r in planned['discs'])
    assert [r for r in planned['discs'][0]['reasons'] if r.startswith('配队 · ')] == ['配队 · 日常','配队 · 爆发']
    request, wanted = confirmation(1, '爆发', cfg, planned, cn, preview_only=True, names=names)
    assert len(wanted) == 6 and all(t['ownerState'] == 'unknown' for t in wanted)
    # Run the normal manual task, replacing only the game/driver boundaries.
    scanner = Obj(config=Obj(scan_drive_disk=True), ocr_worker=Obj(scanned_discs=readings), _open_export=None)
    run = Obj(is_context_running=True, get_application=lambda *_:scanner)
    ctx = Obj(current_instance_idx=1, run_context=run)
    manager = Obj(group=MANUAL_GROUP, root=root, ctx=ctx, account=1, round_success=lambda msg:msg,
                  round_fail=lambda msg, **kwargs:(msg, kwargs.get('data')))
    def scan(*_):
        save(temp/'.debug/inventory_exports'/f'inventory_{scan.count}.json', dict(format='ZOD', version=1, dbVersion=2,
             source='Zenless Optimizer', discs=readings, characters=[], wengines=[], audit=dict(game_total=7,exported=7,delta=0)))
        scan.count += 1
        return Obj(success=True)
    scan.count = 0
    def equip(_, cfg, report, snapshot, ui, *, name, names):
        from drive_disc_manager.loadout import target_loadout
        targets = target_loadout(cfg, report, snapshot, name, names=names)
        assert [t['selectionFp'] for t in targets] == [t['selectionFp'] for t in wanted]
        for num in ('4', '7'):
            row = next(r for r in report['discs'] if r['exportUid'] == num)
            assert row['decision'] == '保留' and row['keptBeforeReserves']
        return dict(profile=name, verified=[t['fp'] for t in targets])
    with (patch('drive_disc_manager.manager_app.manual_request', side_effect=lambda *_:(request, lambda: None)),
          patch('drive_disc_manager.manager_app.wiki_path', return_value=wiki_path),
          patch('drive_disc_manager.scanner_status.scanner_status', return_value=(True,'')),
          patch('drive_disc_manager.manager_app.checked_scan', side_effect=scan),
          patch('drive_disc_manager.scan_marks.capture_marks', return_value=nullcontext()),
          patch('drive_disc_manager.equip_ui.EquipUI'), patch('drive_disc_manager.loadout.apply_loadout', side_effect=equip) as apply,
          patch('drive_disc_manager.optimizer.candidate_pool', side_effect=AssertionError('unexpected second optimizer run'))):
        first = ManagerApp.manage_inventory(manager)
        assert first == '爆发 · 已复读 6 个号位', first
        readings[-1] = dict(spare, location='Lycaon', ownerState='worn')
        msg, change = ManagerApp.manage_inventory(manager)
        assert '变化' in msg and change['targets'][3]['location'] == 'Lycaon' and apply.call_count == 1
        assert any(r[0].endswith('4 号位') and r[1:] == ('装备者','穿戴者待复核','莱卡恩')
                   for r in changed_slots(wanted, change['targets'], names, {'Lycaon':'莱卡恩'}))
        request = change['revised']
        assert ManagerApp.manage_inventory(manager) == '爆发 · 已复读 6 个号位' and apply.call_count == 2
    from drive_disc_manager.inventory_comparison import compare_game_scan
    checked = read(root/'inventory.json')
    replacement = dict(cn, discs=[dict(d, id='101') if d['id']=='1' else d for d in cn['discs']])
    history = compare_game_scan(replacement, checked, names)
    assert history['matched'] == 6 and '101' not in history['lastWorn'] and history['lastWorn']['7'] == 'Lycaon'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--scan', required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        smoke(args.home, args.scan, Path(folder))
    print('PASS two builds, seven recorded discs, three simulated manual equip runs')
