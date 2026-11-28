"""Save a party and copy it to a second profile in an offscreen OneDragon window."""
import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


def smoke(host, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
    from PySide6.QtWidgets import QApplication, QInputDialog
    from qfluentwidgets import PrimaryPushButton
    from one_dragon.utils import os_utils
    from drive_disc_manager.combat_windows import window_requests
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, save
    from drive_disc_manager.window_editor import WindowEditor

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    account = temp/'accounts'/'1'
    actors = [('Lead','配装角色'), ('Support','队友甲'), ('Stun','队友乙'), ('Other','未拥有')]
    save(temp/'plugin_data/wiki_data/zzz_translation.json', dict(character={
        str(i):dict(code=code,EN=code,CHS=name) for i,(code,name) in enumerate(actors)}))
    team = dict(name='常态队', agentKey='Lead', request=dict(characterName='配装角色', roleSystem='ATTACK'))
    save(account/'config.json', dict(config(account), profiles=[team]))
    save(account/'owned-agents.json', ['Lead', 'Support', 'Stun'])
    save(account/'challenge-preferences.json', {'agents': {'配装角色': {'mindscape': 6}, '新队友': {'mindscape': 4}, '队友甲': {'mindscape': 3}}})
    save(account/'network-inventory.json', dict(networkPreview=True, characters=[dict(key='Support', mindscape=0), dict(key='Stun', mindscape=2)]))
    widget = ManagerInterface(SimpleNamespace(current_instance_idx=1,get_plugin_data_dir=lambda _:account.parent))
    widget.on_interface_shown()
    widget.navigation.open('我的配队')
    widget.show()
    app.processEvents()
    actions = {a.text():a for a in widget.profile_menu.menu().menuActions()}
    actions['队伍成员'].trigger()
    editor = widget.squad_editor
    assert widget.team_views.currentWidget() is editor and editor.actors[1].findText('配装角色') == -1
    assert editor.actors[1].findText('未拥有') == -1
    assert editor.ranks[0].value() == 6
    assert editor.tolerance.isVisible() and editor.tolerance.value() == .5
    editor.tolerance.setValue(.25)
    editor.actors[1].setCurrentText('新队友')
    assert editor.ranks[1].value() == 4
    editor.actors[1].setCurrentText('队友甲')
    assert editor.ranks[1].value() == 0
    editor.ranks[1].setValue(6)
    editor.findChild(PrimaryPushButton).click()
    assert config(account)['profiles'][0]['squad'] == [dict(agent='队友甲', mindscape=6)]
    assert '队友甲 6影' in widget.team_score.text()

    def choose_buff(dialog):
        dialog.enabled.setChecked(True)
        dialog.add_buff({})
        row = dialog.buffs
        row.cellWidget(0,0).setCurrentText('队友甲')
        row.cellWidget(0,1).setValue(6)
        row.cellWidget(0,2).setText('爆发加成')
        row.cellWidget(0,4).setValue(15)
        dialog.commit()
        return dialog.result()

    with patch.object(WindowEditor, 'exec', choose_buff):
        actions['输出窗口'].trigger()
    original, = config(account)['profiles']
    assert original['windows'][0]['buffs'][0]['member'] == '队友甲'
    assert window_requests(original)[0][2]['combatExtraStats'] == {'CRIT_RATE':15}
    with patch.object(QInputDialog,'getText',return_value=('换人队',True)):
        actions['复制为另一配队'].trigger()
    actions['队伍成员'].trigger()
    assert editor.ranks[1].value() == 6
    assert editor.tolerance.value() == .25
    editor.tolerance.setValue(0)
    editor.actors[1].setCurrentText('队友乙')
    assert editor.ranks[1].value() == 2
    editor.findChild(PrimaryPushButton).click()
    original_again, other = config(account)['profiles']
    assert original_again['tolerancePct'] == .25 and other['tolerancePct'] == 0
    assert original == original_again and other['squad'] == [dict(agent='队友乙', mindscape=2)]
    assert window_requests(other)[0][2]['combatExtraStats'] == {}
    assert '队友乙 2影' in widget.team_score.text()
    second_account = account.parent/'2'
    save(second_account/'config.json', dict(config(second_account), profiles=[team]))
    save(second_account/'challenge-preferences.json', {'agents': {'配装角色': {'mindscape': 1}, '另一位队友': {}}})
    widget.ctx.current_instance_idx = 2
    widget.on_interface_shown()
    widget.navigation.open('我的配队')
    actions['队伍成员'].trigger()
    assert editor.ranks[0].value() == 1 and editor.actors[1].findText('新队友') == -1
    assert editor.actors[1].findText('另一位队友') > 0
    widget.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as temp:
        smoke(parser.parse_args().home.resolve(), Path(temp))
    print('PASS embedded party editor, save window buff, copy team and replace support')
