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
    save(root/'scan-observations.json', dict(at=source.stat().st_mtime, total=read(source)['audit']['game_total'], rows=readings))

    def choose(_url, profile, pool, _current):
        assert _current == set('123456')  # Six worn discs from one partial OCR scan.
        ids = ['1', '2', '3', '4' if profile['name'] == '日常' else '7', '5', '6']
        assert set(ids) <= {d['id'] for d in pool}
        return [dict(ids=ids, outputScore=100, critRate=100)]

    with (patch('drive_disc_manager.engine.managed', return_value=nullcontext('fixture')),
          patch('drive_disc_manager.optimizer.candidate_pool', side_effect=choose)):
        planned = preview(root, wiki_path, refresh=False)
    assert all(r['decision'] == '保留' for r in planned['discs'])
    assert planned['assignments']['日常']['baselineSource'] == 'scan'
    assert all(row['ownerState'] == 'unknown' for row in planned['discs'])
    assert [r for r in planned['discs'][0]['reasons'] if r.startswith('配队 · ')] == ['配队 · 日常','配队 · 爆发']
    request, wanted = confirmation(1, '爆发', cfg, planned, cn, preview_only=True, names=names)
    assert len(wanted) == 6 and all(t['ownerState'] == 'unknown' for t in wanted)
    # Run the normal manual task, replacing only the game/driver boundaries.
    scanner = Obj(config=Obj(scan_drive_disk=True), ocr_worker=Obj(scanned_discs=readings), _open_export=None)
    run = Obj(is_context_running=True, get_application=lambda *_:scanner)
    ctx = Obj(current_instance_idx=1, run_context=run)
    manager = Obj(group=MANUAL_GROUP, root=root, ctx=ctx, account=1, round_success=lambda msg:msg,
                  round_fail=lambda msg, **kwargs:(msg, kwargs.get('data')))
    def observations(_, total, guard):
        assert total == 7
        for idx in (0,1,2,6,4,5):
            guard()
            yield readings[idx], lambda: dict(readings[idx], id='next-scanner-reading')
        raise AssertionError('visited an unrelated disc after all six targets were found')
    class UI:
        def guard(self): pass
        def inspect(self, target):
            return 'equipped' if owners.get(target['selectionFp']) == target['agent'] else 'available'
        def equip(self, target, checkpoint):
            checkpoint()
            owners[target['selectionFp']] = target['agent']
            moves.append(target['selectionFp'])
    owners = {t['selectionFp']:'Soldier 11' for t in wanted if t['slot'] != 4}
    moves = []
    with (patch('drive_disc_manager.manager_app.manual_request', side_effect=lambda *_:(request, lambda: None)),
          patch('drive_disc_manager.manager_app.wiki_path', return_value=wiki_path),
          patch('drive_disc_manager.scanner_status.scanner_status', return_value=(True,'')),
          patch('drive_disc_manager.manager_app.checked_scan', side_effect=AssertionError('unexpected full scan')),
          patch('drive_disc_manager.equip_targets.warehouse_observations', side_effect=observations),
          patch('drive_disc_manager.equip_ui.EquipUI', return_value=UI()),
          patch('drive_disc_manager.optimizer.candidate_pool', side_effect=AssertionError('unexpected second optimizer run'))):
        first = ManagerApp.manage_inventory(manager)
        assert first == '爆发 · 已复读 6 个号位' and moves == ['cn-uid/7'], first
        assert [t['selectionFp'] for t in read(root/'equip-result.json')['targets']] == [t['selectionFp'] for t in wanted]
        assert not (root/'inventory.json').exists()
        readings[-1] = dict(spare, location='Lycaon', ownerState='worn')
        owners['cn-uid/7'] = 'Lycaon'
        msg, change = ManagerApp.manage_inventory(manager)
        assert '变化' in msg and change['targets'][3]['location'] == 'Lycaon' and len(moves) == 1
        assert any(r[0].endswith('4 号位') and r[1:] == ('装备者','穿戴者待复核','莱卡恩')
                   for r in changed_slots(wanted, change['targets'], names, {'Lycaon':'莱卡恩'}))
        request = change['revised']
        assert ManagerApp.manage_inventory(manager) == '爆发 · 已复读 6 个号位' and len(moves) == 2
        readings[-1] = dict(spare, markState='marked', trash=True)
        msg, detail = ManagerApp.manage_inventory(manager)
        assert '已标记弃置' in msg and detail is None and len(moves) == 2
    readings[-1] = dict(spare, location='Lycaon', ownerState='worn')
    from drive_disc_manager.inventory_comparison import compare_game_scan, verify_cn_inventory
    checked = verify_cn_inventory(cn, dict(source='Zenless Optimizer', audit=dict(game_total=7)), readings, names)
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
    print('PASS two builds, seven recorded discs: early stop, donor change, marked target, original CN IDs')
