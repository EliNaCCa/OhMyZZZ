"""Replay five archived stat lines in an isolated account. No game input."""
from argparse import ArgumentParser
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch


def smoke(home, sample, root):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from one_dragon.utils import os_utils
    from drive_disc_manager import incremental_marks as delta
    from drive_disc_manager.cn_export import refresh
    from drive_disc_manager.inventory_comparison import verify_cn_inventory
    from drive_disc_manager.live import Marker
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import config, equipment_names, read, save

    os_utils.set_work_dir(str(root.parent))
    path = root/'exports'/'zzz-drives-zood-v1.json'
    recorded = read(sample)['discs'][:5]
    readings, raws = [], []
    for i, disc in enumerate(recorded):
        locked = i != 4
        reading = dict(disc, location='', ownerState='bag', lock=locked, lockState='locked' if locked else 'unlocked',
                       markState='unmarked', trash=False, observedGrade='S')
        readings.append(reading)
        attrs = {k:reading[k] for k in ('level','rarity','slotKey','mainStatKey','substats')}
        raws.append(dict(attrs, id=str(1001+i), setKey='TheSkyAblaze', lock=locked, trash=False, location=''))
    def export(rows):
        save(path, dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=rows))

    names = equipment_names({})
    cfg = dict(config(root), auto_sync=False, protect15=False, protectNaturalCrit=False, reserve=0,
               trimRecommended=True, profiles=[], includeOwned=False, inventorySource='cn_export', applyMarkers=True)
    save(root/'config.json', cfg)
    save(root/'rules.json', dict(version='sample', fields={'matrix/TheSkyAblaze/4':['crit_']}))
    export(raws[:3])
    refresh(root, lambda:None)
    initial = read(root/'cn-inventory.json')
    view = verify_cn_inventory(initial, dict(source='Zenless Optimizer', audit=dict(game_total=3)), readings[:3], names)
    save(root/'inventory.json', view)
    planned = preview(root, refresh=False)
    baseline = preview(root, refresh=False, verified_game=True, cn_plan=planned)
    delta.checkpoint(root, initial, planned, view, baseline, NS(observed={}, confirmed_locks={}))

    scanner = NS(config=NS(scan_drive_disk=True), drive_disk_scanner=NS(parser=None), _ensure_screen_areas=lambda:None)
    app = NS(root=root, account=1, group='one_dragon', ctx=NS(current_instance_idx=1,
             controller=NS(game_win=NS(is_win_active=True)), run_context=NS(is_context_running=True, get_application=lambda *a:scanner)),
             round_success=lambda s:NS(ok=True, msg=s), round_fail=lambda s:NS(ok=False, msg=s))
    visited, scheduled = [], []
    def pages(scanner, total, guard, *, by_acquisition=False):
        assert by_acquisition
        guard()
        assert total == 5
        for index in (3,0,1,4,2):  # The other drop is behind consecutive old locks.
            visited.append(index)
            yield readings[index], lambda:readings[index]
        raise AssertionError('scanned past the final required item')

    def run(marker):
        scheduled.append({mode:{r['exportUid'] for r in marker.phase_targets(mode).values()} for mode in ('lock','mark')})
        return marker.observed

    native = NS(dismantle_abandon=True, dismantle_level='A及以下')
    # The new unlocked disc already has a player-applied discard mark, absent in the export.
    readings[4].update(trash=True, markState='marked')
    export(raws)
    with ExitStack() as stack:
        for target, value in [('scanner_status.scanner_status',(True,'')), ('warehouse.enter_warehouse',5),
                              ('task_order.following_native',(native,'day')), ('dismantle.following_native',(native,'day'))]:
            stack.enter_context(patch('drive_disc_manager.'+target, return_value=value))
        stack.enter_context(patch('drive_disc_manager.equip_targets.warehouse_observations', pages))
        stack.enter_context(patch.object(Marker, 'run', run))
        result = ManagerApp.manage_inventory(app)
        assert result.ok and '2 张' in result.msg, result.msg
        assert visited == [3,0,1,4] and scheduled[-1] == {'lock':{'1004'}, 'mark':set()}, (visited, scheduled)
        receipt = read(root/'dismantle-preview.json')
        assert [e['uid'] for e in receipt['entries']] == ['1005'] and len(receipt['beforeUids']) == 5
        assert read(root/'inventory.json') == view
        again = ManagerApp.manage_inventory(app)
        assert not again.ok and '刷新' in again.msg and len(visited) == 4, again.msg
        # A newer export, same inventory. With no native task only the warehouse count is needed.
        export(raws)
        visited.clear()
        with patch('drive_disc_manager.task_order.following_native', return_value=None), patch('drive_disc_manager.dismantle.following_native', return_value=None):
            result = ManagerApp.manage_inventory(app)
            assert result.ok and not visited and not scheduled[-1]['mark'], (result.msg, visited)
        # Old UID changes intent even when its inventory attributes and lock stay identical.
        cn = read(root/'cn-inventory.json')
        authoritative = preview(root, refresh=False)
        authoritative['discs'][0]['reasons'].append('手动保护')
        groups, wanted = delta.worklist(cn, authoritative, read(root/delta.FILE)['facts'], names, None)
        assert wanted == {'1001'}, wanted
        # An identical new copy cannot be matched by its stat line, even with a different UID.
        pair = next(k for k,v in groups.items() if v == ['1001'])
        groups[pair].append('9999')
        assert delta.find_readings(cn, names, groups, {'1001','9999'}, []) == {}


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--sample', type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory() as temp:
        smoke(args.home, args.sample, Path(temp)/'1')
    print('PASS daily CN delta, early stop after two locks, native manual discard, stale export, unchanged inventory and lookalike')
