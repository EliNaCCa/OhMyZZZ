"""One account, a few recorded disc attributes, and test-only IDs in a temporary export."""
import argparse
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
    from drive_disc_manager import cn_export as cn
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    root = temp / 'accounts' / '1'
    reader = temp / 'reader'
    reader.mkdir()
    save(root/'config.json', dict(config(root), inventorySource='cn_export', auto_sync=False))
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _: root.parent,
                          run_context=SimpleNamespace(is_context_stop=True, is_context_running=True))
    view = ManagerInterface(ctx)
    view.on_interface_shown()
    with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(reader)):
        view.export_folder.click()
    path = reader / 'exports' / cn.EXPORT_NAME
    assert view.cn_file == path and view.preview_job is None and not path.exists()
    other = root.parent / '2'
    other.mkdir()
    try:
        cn.select_folder(other, reader, lambda: None)
    except ValueError as exc:
        assert '另一个' in str(exc)
    else:
        raise AssertionError('assigned the pending folder to two accounts')

    observations = read(sample)['discs'][:3]
    assert len(observations) == 3 and all(d['setKey'] == '拂晓行纪' for d in observations)
    rows = [dict({k:d[k] for k in ('slotKey', 'level', 'rarity', 'mainStatKey', 'substats')},
                 id=str(1001+i), setKey='TheSkyAblaze', location='', trash=False, lock=True)
            for i, d in enumerate(observations)]
    data = dict(format='ZOOD', version=1, source=cn.SOURCE, discs=rows)
    save(path, data)
    view.on_interface_shown()
    deadline = monotonic() + 15
    while view.preview_job and monotonic() < deadline:
        app.processEvents()
        sleep(.01)
    assert len(view.rows) == 3, view.preview_note.text()
    snap = read(root/cn.FILE)
    assert [d['observedValues'] for d in snap['discs']] == [d['observedValues'] for d in observations]
    with patch.object(view, 'start_preview', side_effect=AssertionError('reread an unchanged file')):
        view.on_interface_shown()

    manager = SimpleNamespace(ctx=ctx, root=root, account=1, group='one_dragon', round_success=lambda msg: msg,
                              round_fail=lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
    rows[0]['lock'] = False
    save(path, data)
    assert '预览' in ManagerApp.manage_inventory(manager)
    snapshot = read(root/cn.FILE)
    assert snapshot['lastChange'] == '锁定变化 1 张' and not snapshot['discs'][0]['lock']
    save(path, dict(data, discs=[dict(row, id=str(5000+i)) for i, row in enumerate(rows)]))
    try:
        cn.refresh(root, lambda: None)
    except ValueError as exc:
        assert '没有同一张盘' in str(exc)
    else:
        raise AssertionError('adopted another inventory')
    save(path, data)
    try:
        cn.refresh(root, lambda: save(path, data))
    except ValueError as exc:
        assert '正在更新' in str(exc)
    else:
        raise AssertionError('published a file replaced during import')
    assert snapshot == read(root/cn.FILE)
    print('OK: folder before export, 3 preview rows, daily refresh, account conflict and replacement')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', type=Path)
    parser.add_argument('sample', type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        smoke(args.home, args.sample, Path(directory))
