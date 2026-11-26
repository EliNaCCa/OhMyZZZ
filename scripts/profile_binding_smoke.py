"""Refresh a legacy team in a temporary account using the real settings dialog."""
import argparse
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, root):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication, QMessageBox
    from one_dragon.utils import os_utils
    from drive_disc_manager.loadout import verify_bindings
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, save

    app = QApplication([])
    os_utils.set_work_dir(str(home))
    account = root/'1'
    dictionary = {'character': {'1': {'EN': 'Ellen Joe', 'CHS': '艾莲', 'code': 'Ellen'}}}
    path = root/'wiki.json'
    save(path, dictionary)
    target = dict(sourceId='1', agent='Ellen', requiresClaimMapping=False,
                  request=dict(characterName='艾莲', roleSystem='ATTACK'))
    team = dict(name='常态队', request=target['request'], sourceId='1', simultaneous='A',
                tolerancePct=.3, windows=[dict(name='常态', weight=1, buffs=[])], mainWindow='常态')
    cfg = dict(config(account), profiles=[team], fieldProfile='常态队')
    save(account/'config.json', cfg)
    widget = ManagerInterface(SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:root))
    widget.on_interface_shown()
    with patch('drive_disc_manager.settings.wiki_path', return_value=path), \
         patch.object(widget, 'optimizer_session', return_value=nullcontext('fixture')), \
         patch('drive_disc_manager.optimizer.saved_targets', return_value=[target]):
        for old, accept in [('', False), ('', True), ('Anby', True)]:
            before = dict(cfg, profiles=[dict(team, agentKey=old)])
            save(account/'config.json', before)
            widget.reload()
            widget.teams.selectRow(0)
            def respond(dialog):
                assert (old or '未绑定')+' → Ellen' in dialog.informativeText()
                assert '换装角色绑定' in dialog.detailedText() and dialog.defaultButton().text() == '取消'
                next(b for b in dialog.buttons() if b.text() == ('更新配队' if accept else '取消')).click()
            with patch.object(QMessageBox, 'exec', respond):
                widget.refresh_profile()
            expected = dict(cfg, profiles=[dict(team, agentKey='Ellen')]) if accept else before
            assert config(account) == expected
    correct = config(account)
    verify_bindings(correct, team['name'], dictionary)
    peer = dict(team, name='同队其他方案', agentKey='Anby')
    try:
        verify_bindings(dict(correct, profiles=[*correct['profiles'], peer]), team['name'], dictionary)
    except ValueError as exc:
        assert peer['name'] in str(exc)
    else:
        raise AssertionError('a mismatched teammate passed the equip preflight')
    widget.close()
    app.processEvents()


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(args.parse_args().home.resolve(), Path(tmp))
    print('PASS legacy team refresh, confirmed role change and teammate preflight')
