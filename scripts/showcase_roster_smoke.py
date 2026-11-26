"""Read a small showcase fixture from the normal embedded team page, without a disc export."""
import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(host, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
    from PySide6.QtWidgets import QApplication, QInputDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, known_agents, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    root = temp/'accounts/1'
    uid = '123456789'
    wiki = dict(character={'1141': dict(CHS='莱卡恩', EN='Lycaon', code='Lycaon'),
                           '1281': dict(CHS='朱鸢', EN='Zhu Yuan', code='ZhuYuan')})
    save(temp/'plugin_data/wiki_data/zzz_translation.json', wiki)
    save(root/'challenge-preferences.json', dict(agents={'莱卡恩': dict(mindscape=2)}))
    avatar = dict(Id=1141, Level=60, TalentLevel=6, Weapon=dict(Id=14114, BreakLevel=4, UpgradeLevel=5))
    hidden = dict(Id=1281, IsHidden=True, Level=60, TalentLevel=6)
    payload = dict(uid=uid, PlayerInfo=dict(ShowcaseDetail=dict(AvatarList=[avatar, hidden, {'Id':1401}])) )
    widget = ManagerInterface(SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:root.parent))
    widget.on_interface_shown()
    widget.navigation.open('关卡分队')
    page = widget.challenge
    with patch.object(QInputDialog, 'getText', return_value=(uid, True)), patch('drive_disc_manager.showcase.fetch', return_value=payload) as fetch:
        for _ in range(2):
            page.read_agents.click()
            end = monotonic() + 8
            while root in widget.showcase_jobs and monotonic() < end:
                app.processEvents()
                sleep(.01)
            assert page.read_agents.isEnabled() and '2 位' in page.status.text(), page.status.text()
        fetch.assert_called_once_with(uid)
    assert '莱卡恩（6+5）' in page.roster.text() and '朱鸢' not in page.roster.text()
    assert '60级' in page.roster.toolTip() and known_agents(root) >= {'1141', '1401'}
    assert read(root/'showcase-agents.json')['training']['1401'] == {}
    save(root/'config.json', dict(config(root), showcaseUid='987654321'))
    widget.show_challenge()
    assert page.pool['莱卡恩']['mindscape'] == 2 and 'engineRank' not in page.pool['莱卡恩']
    widget.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    with TemporaryDirectory() as folder:
        smoke(parser.parse_args().home.resolve(), Path(folder))
    print('PASS: embedded roster read, UID-scoped cache, zero disc exports, two visible agents')
