"""Replay up to three saved drops through the daily task and its embedded result view."""
import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch


def smoke(home, sample):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication
    from one_dragon.utils import os_utils
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, read, save

    qt = QApplication([])
    os_utils.set_work_dir(str(home))
    observed = read(sample)
    discs = observed['discs'][:3]
    assert discs and observed['status'] == 'verified'
    with TemporaryDirectory() as directory:
        root = Path(directory)/'1'
        cfg = dict(config(root), inventorySource='cn_export', auto_sync=False, applyMarkers=True)
        save(root/'config.json', cfg)
        src = sample.parent
        for file in ('rules.json', 'owned-agents.json'):
            if (src/file).is_file():
                save(root/file, read(src/file))
        scanner = NS(config=NS(scan_drive_disk=True), _ensure_screen_areas=lambda: None)
        ctx = NS(current_instance_idx=1, get_plugin_data_dir=lambda _: root.parent,
                 controller=NS(game_win=NS(is_win_active=True)),
                 run_context=NS(is_context_running=True, is_context_stop=False,
                                is_app_registered=lambda _:True, get_config=lambda *_:scanner.config,
                                get_application=lambda *_:scanner))
        class Manager:
            manage_inventory = ManagerApp.manage_inventory
            recent_when_waiting = ManagerApp.recent_when_waiting
            round_fail = staticmethod(str)
            round_success = staticmethod(lambda _: (_ for _ in ()).throw(AssertionError('partial recorded as complete')))
        manager = Manager()
        manager.root, manager.account, manager.ctx, manager.group = root, 1, ctx, 'one_dragon'
        with (patch('drive_disc_manager.recent_discs.leave_disc_detail'),
              patch('drive_disc_manager.warehouse.enter_warehouse', return_value=observed['warehouseCount']),
              patch('drive_disc_manager.recent_discs.read_front_row', return_value=discs) as front):
            result = manager.manage_inventory()
            assert f'已记录最近获得的 {len(discs)} 张盘' in result, result
            assert front.call_count == 1 and front.call_args.args[-1] == 3
            original = read(root/'recent-discs.json')
            assert original['discs'] == discs and all(r['suggestion'] for r in original['rows'])
            save(root/'config.json', dict(cfg, inventorySource='network'))
            with patch('drive_disc_manager.network_reader.socket.create_connection', side_effect=ConnectionRefusedError()):
                assert '已记录最近获得的' in manager.manage_inventory()
            saved = read(root/'recent-discs.json')
            assert saved['rows'] == original['rows'] and front.call_count == 2
            with patch('drive_disc_manager.network_reader.capture', side_effect=ValueError('账号不符')):
                assert manager.manage_inventory() == '账号不符' and front.call_count == 2
            assert read(root/'recent-discs.json') == saved
            for f in ('inventory.json', 'cn-inventory.json', 'network-inventory.json', 'markers.json', 'upgrade-result.json'):
                assert not (root/f).exists(), f
        ctx.run_context.is_context_running = False
        ctx.run_context.is_context_stop = True
        page = ManagerInterface(ctx)
        page.on_interface_shown()
        assert page.recent_result.isEnabled()
        page.recent_result.trigger()
        assert all(r['name'] in page.probe_note.text() and r['suggestion'] in page.probe_note.text() for r in saved['rows'])
        ctx.current_instance_idx = 2
        page.on_interface_shown()
        assert not page.recent_result.isEnabled() and not page.probe_note.text()
        print('PASS missing CN file, offline reader, account mismatch, saved rows in embedded UI; other account empty')
        print([(r['name'],r['slot'],r['suggestion']) for r in saved['rows']])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', type=Path)
    parser.add_argument('sample', type=Path)
    args = parser.parse_args()
    smoke(args.home.resolve(), args.sample.resolve())
