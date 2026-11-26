"""Import a few ZOOD records from the ordinary OneDragon page (offscreen)."""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, sample, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home / 'src')]
    from PySide6.QtWidgets import QApplication, QFileDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager.cn_export import refresh
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, inventory, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    account = temp / 'accounts' / '1'
    file = temp / 'export.json'
    original = read(sample)['discs'][:5]
    records = []
    for i, observed in enumerate(original):
        # Recorded OCR attributes, test-only IDs and exporter spelling.
        raw = {k: observed[k] for k in ('level', 'rarity', 'slotKey', 'mainStatKey', 'substats')}
        assert observed['setKey'] == '拂晓行纪'
        records.append(dict(raw, id=str(1001+i), setKey='TheSkyAblaze', location='', lock=True, trash=False))
    exported = dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=records)
    save(file, exported)
    save(account / 'config.json', dict(config(account), auto_sync=False))
    legacy = dict(networkPreview=True, characters=[dict(key='Anton', mindscape=4)])
    save(account / 'network-inventory.json', legacy)
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _: account.parent,
                          run_context=SimpleNamespace(is_context_stop=True, is_context_running=True))
    widget = ManagerInterface(ctx)
    widget.on_interface_shown()
    widget.reader.setCurrentIndex(widget.reader.findData('cn_export'))
    assert '选择国服' in widget.scan_button.text()
    with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(file), '')):
        widget.scan_button.click()
    end = monotonic() + 12
    while widget.preview_job is not None and monotonic() < end:
        app.processEvents()
        sleep(.01)
    assert len(widget.rows) == len(original) == 5, widget.preview_note.text()
    snap_path = account / 'cn-inventory.json'
    snapshot = read(snap_path)
    assert '刷新导出文件' == widget.scan_button.text()
    for record, source in zip(snapshot['discs'], original):
        assert record['observedValues'] == source['observedValues']
        assert record['ownerState'] == record['markState'] == 'unknown'
    try:
        inventory(snapshot)
    except ValueError:
        pass
    else:
        raise AssertionError('stats-only file treated as a verified game scan')
    manager = SimpleNamespace(ctx=ctx, root=account, account=1, group='one_dragon', round_success=lambda msg: msg,
                              round_fail=lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
    assert '预览' in ManagerApp.manage_inventory(manager)
    assert read(snap_path) == snapshot and not (account / 'inventory.json').exists()
    assert read(account / 'network-inventory.json') == legacy
    from drive_disc_manager.cn_export import normalize
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import equipment_names
    pair = [records[0], dict(records[0], id='9999')]
    seen = []
    def observe_pool(url, profile, pool, worn):
        assert not worn
        seen.extend(d['id'] for d in pool)
        return []  # A pair of 1-slot pieces cannot form a six-slot build.
    fixture = normalize(dict(exported, discs=pair))
    cfg = dict(config(account), profiles=[dict(name='样盘', request=dict(characterName='安东'))])
    plan(fixture, cfg, {}, equipment_names({}), observe_pool)
    assert seen == ['1001', '9999']
    # Replay one simulated enhancement on one test ID. The real game was not changed.
    changed = deepcopy(exported)
    changed['discs'][0]['level'] = 12
    changed['discs'][0]['substats'][2]['upgrades'] -= 1
    save(file, changed)
    assert refresh(account, lambda: None) == '属性变化 1 张'
    save(file, exported)
    assert refresh(account, lambda: None) == '属性变化 1 张'
    assert refresh(account, lambda: None) == '驱动盘没有变化'
    good = snap_path.read_bytes()
    changed['discs'][0]['id'] = changed['discs'][1]['id']
    save(file, changed)
    try:
        refresh(account, lambda: None)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted duplicate item ID')
    assert snap_path.read_bytes() == good
    widget.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--sample', required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        smoke(args.home.resolve(), args.sample.resolve(), Path(folder))
    print('PASS: five OCR reference discs, ZOOD import UI, scheduled refresh, ID-based enhancement and duplicate rejection')
