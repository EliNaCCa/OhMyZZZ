"""One CN export arriving while the settings page is open. No game input."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
host = Path(sys.argv[1])
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
from PySide6.QtWidgets import QApplication
from one_dragon.utils import os_utils
from drive_disc_manager.cn_export import EXPORT_NAME
from drive_disc_manager.manager_app import ManagerApp
from drive_disc_manager.reader_region import align_source, cn_client
from drive_disc_manager.settings import ManagerInterface
from drive_disc_manager.store import config, read, save


def until(app, predicate):
    end = monotonic() + 8
    while monotonic() < end:
        app.processEvents()
        if predicate():
            return
        sleep(.02)
    raise AssertionError('the CN export was not imported')


with TemporaryDirectory() as tmp:
    base = Path(tmp)
    os_utils.set_work_dir(str(base))
    client = base/'ZenlessZoneZero.exe'
    label = base/'version_info'
    label.write_text('CNPRODWin3.3.0', encoding='ascii')
    account = base/'accounts'/'1'
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:account.parent,
                          game_account_config=SimpleNamespace(game_path=str(client)),
                          run_context=SimpleNamespace(is_context_stop=True, is_context_running=True))
    app = QApplication([])
    old = dict(inventorySource='network', auto_sync=False, reserve=5)
    save(account/'config.json', old)
    widget = ManagerInterface(ctx)
    widget.export_poll.setInterval(50)
    widget.on_interface_shown()
    assert widget.reader.findData('network') == -1 and widget.reader.currentData() == 'cn_export'
    assert read(account/'config.json') == dict(old, inventorySource='cn_export')
    # Real observed attributes; only the temporary fixture's UID and game state are invented.
    observation = read(host/'plugin_data/drive_disc_manager/1/recent-discs.json')['discs'][0]
    fields = ('setKey', 'slotKey', 'level', 'rarity', 'mainStatKey', 'substats')
    disc = dict({key:observation[key] for key in fields}, id='101', location='', lock=False, trash=False)
    data = dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=[disc])
    export = account/'exports'/EXPORT_NAME
    save(export, data)
    until(app, lambda:len(widget.rows) == 1 and widget.preview_job is None)
    assert read(account/'cn-inventory.json')['discs'][0]['observedValues'] == observation['observedValues']
    widget.on_interface_hidden()
    assert not widget.export_poll.isActive()
    # A headless daily invocation must choose CN even without having opened settings.
    save(account/'config.json', old)
    task = SimpleNamespace(ctx=ctx, account=1, root=account, group='one_dragon', round_success=str,
                           round_fail=lambda message:(_ for _ in ()).throw(AssertionError(message)))
    with patch('drive_disc_manager.network_reader.capture', side_effect=AssertionError('overseas reader called')):
        assert '预览' in ManagerApp.manage_inventory(task)
    assert config(account)['inventorySource'] == 'cn_export'
    save(account/'config.json', dict(old, inventorySource='ocr'))
    assert align_source(ctx, account) and config(account)['inventorySource'] == 'ocr'
    for version in ('OSPRODWin3.3.0', 'CNPRODWin3x3x0'):
        label.write_text(version, encoding='ascii')
        assert not cn_client(ctx)
    widget.close()
    print('PASS CN settings, incoming one-disc JSON, headless daily routing and explicit OCR choice')
