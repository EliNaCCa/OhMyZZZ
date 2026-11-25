"""One rule and one quota edited in a temporary OneDragon account, without a game."""
import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, tmp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication, QInputDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager import subscriptions
    from drive_disc_manager.quota_editor import QuotaEditor
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, save

    app = QApplication([])
    os_utils.set_work_dir(str(home))
    account = tmp/'1'
    key = 'matrix/SwingJazz/5'
    save(account/'rules.json', dict(fields={key:['atk_']}, version='fixture'))
    context = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:tmp)
    widget = ManagerInterface(context)
    widget.on_interface_shown()
    started, proceed = Event(), Event()

    def source():
        started.set()
        assert proceed.wait(5)
        return {key:['def_']}, {'Set+Main':'42'}, {key:['Set+Main']}

    def finish():
        proceed.set()
        deadline = monotonic()+5
        while widget.rule_updates and monotonic() < deadline:
            app.processEvents()
            sleep(.005)
        assert not widget.rule_updates

    with patch.object(subscriptions, 'sources', side_effect=source) as fetch:
        widget.update_rules()
        assert started.wait(1) and not widget.sync_button.isEnabled()
        widget.update_rules()
        assert fetch.call_count == 1
        widget.rule_search.setText('5号')
        assert widget.visible_keys == [key]
        widget.editor.setPlainText('暂存的配置')
        finish()
        assert widget.rules['fields'][key] == ['def_'] and widget.editor.toPlainText() == '暂存的配置'

    def switch(*args):
        context.current_instance_idx = 2
        widget.on_interface_shown()
        return '继承上游', True

    with patch.object(QInputDialog, 'getItem', side_effect=switch):
        try:
            widget.edit_rule()
        except ValueError as exc:
            assert '账号已切换' in str(exc)
        else:
            raise AssertionError('rule dialog saved after account switch')
    assert not (account/'config.json').exists() and not (tmp/'2/config.json').exists()

    context.current_instance_idx = 1
    run = context.run_context = SimpleNamespace(is_app_registered=lambda key: key == 'inventory_scan')
    option = SimpleNamespace(scan_drive_disk=False)
    def scanner_config(key, idx, group_id):
        assert (key,idx,group_id) == ('inventory_scan',1,'one_dragon')
        return option
    run.get_config = scanner_config
    cfg = config(account)
    group = dict(set='SwingJazz', slot='5', main='atk_')
    cfg['aliases'] = {'SwingJazz':'摇摆爵士'}
    broader = dict(set='摇摆爵士', count=3)
    cfg['reserve_rules'] = [broader, dict(set='摇摆爵士', slot=5, main='atk_', count=6)]
    save(account/'config.json', cfg)
    widget.on_interface_shown()

    assert '开启「驱动盘」' in widget.scanner_note.text()
    option.scan_drive_disk = True
    widget.on_interface_shown()
    assert '已就绪' in widget.scanner_note.text()

    def inherit(dialog):
        assert dialog.custom.isChecked() and dialog.count.value() == 6
        assert '上级规则 3 张' in dialog.inherit.text()
        dialog.inherit.click()
        return True

    with patch.object(QuotaEditor, 'exec', inherit):
        assert widget.set_quota(dict(group, fp='fixture')) == '继承 · 通用'
    assert config(account)['reserve_rules'] == [broader]
    # Create a preset for a non-scanned set, then verify its scope on two different slots.
    page = widget.reserve_presets
    page.suit.setCurrentIndex(page.suit.findData('SwingJazz'))
    assert page.scope() == {'set':'SwingJazz'} and '通用 3 张' in page.current.text()
    page.slot.setCurrentIndex(page.slot.findData('5'))
    assert page.scope() == {'set':'SwingJazz', 'slot':'5'}

    def narrow(dialog):
        dialog.purpose.setCurrentText('偏暴击')
        dialog.custom.click()
        dialog.count.setValue(2)
        dialog.purpose.setCurrentText('偏暴伤')
        dialog.custom.click()
        dialog.count.setValue(4)
        return True

    with patch.object(QuotaEditor, 'exec', narrow):
        page.change.click()
    assert config(account)['reserve_rules'] == [broader, dict(set='SwingJazz', slot='5', purpose='偏暴击', count=2, stats=['crit_']),
                                               dict(set='SwingJazz', slot='5', purpose='偏暴伤', count=4, stats=['crit_dmg_'])]
    assert '偏暴伤 4 张' in page.current.text()
    page.slot.setCurrentIndex(page.slot.findData('4'))
    assert '偏暴伤' not in page.current.text()
    assert page.grid.rowCount() == 3
    widget.close()


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(args.parse_args().home.resolve(), Path(tmp))
    print('PASS embedded settings: responsive sync, account switch and inherited quota')
